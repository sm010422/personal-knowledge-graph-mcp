"""Cypher access layer. Tools in server.py stay thin and call into this.

Graph layout (details in docs/schema.md):
  (:Entity:<Type> {key, type, name, normName, aliases, aliasKeys, aliasText, ...props})
  (:Entity)-[:<RELATION> {validFrom, validTo, provenance, ...props}]->(:Entity)
  (:Note {id, content, source})-[:ABOUT]->(:Entity)
  (:Revision {changedAt, changes})-[:REVISION_OF]->(:Entity)

Labels and relationship types are interpolated into Cypher only from the
EntityType / RelationType enums, never from raw user input.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from neo4j import AsyncDriver, AsyncManagedTransaction

from pkg_mcp import policy
from pkg_mcp.errors import AmbiguousEntity, EntityNotFound, InvalidInput
from pkg_mcp.schema import (
    DeleteResult,
    EntityRef,
    EntityType,
    NoteRef,
    Provenance,
    RelationRef,
    RelationResult,
    RelationType,
    SearchHit,
    UpsertResult,
    check_relation_rule,
    entity_key,
    normalize_name,
    parse_entity_ref,
)

INTERNAL_ENTITY_KEYS = frozenset(
    {
        "key",
        "type",
        "name",
        "normName",
        "aliases",
        "aliasKeys",
        "aliasText",
        "provenance",
        "createdAt",
        "updatedAt",
        "deletedAt",
    }
)
INTERNAL_RELATION_KEYS = frozenset(
    {"validFrom", "validTo", "provenance", "confidence", "createdAt"}
)
_PROPERTY_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')

MAX_CONTEXT_RELATIONS = 200
MAX_CONTEXT_NOTES = 20


# ---- input validation -------------------------------------------------------


def validate_properties(
    properties: dict[str, Any], reserved: frozenset[str], where: str
) -> dict[str, Any]:
    """Neo4j can only store scalars and homogeneous lists of scalars."""
    for key, value in properties.items():
        if not _PROPERTY_KEY.match(key):
            raise InvalidInput(
                f"{where} key '{key}' must be an ASCII identifier (letters, digits, _), "
                "e.g. 'startedAt', 'repoUrl'."
            )
        if key in reserved:
            raise InvalidInput(f"{where} key '{key}' is reserved; use the dedicated parameter.")
        if not _is_storable(value):
            raise InvalidInput(
                f"{where} '{key}' must be a string, number, boolean, null, "
                "or a list of one of those (no nested objects)."
            )
    policy.check_properties(properties, where)
    return properties


def _is_storable(value: Any) -> bool:
    scalar = (str, int, float, bool)
    if value is None or isinstance(value, scalar):
        return True
    if isinstance(value, list):
        kinds = {type(v) for v in value}
        return all(isinstance(v, scalar) for v in value) and len(kinds) <= 1
    return False


def build_fulltext_query(text: str) -> str:
    """Escape Lucene syntax and add a prefix wildcard for latin terms ("kube" -> kube*)."""
    clauses: list[str] = []
    for term in text.split():
        escaped = _LUCENE_SPECIAL.sub(r"\\\1", term)
        clauses.append(escaped)
        if term.isascii() and term.isalnum():
            clauses.append(f"{term.lower()}*")
    return " OR ".join(clauses)


# ---- record conversion ------------------------------------------------------


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.iso_format() if hasattr(value, "iso_format") else str(value)


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    return value.to_native() if hasattr(value, "to_native") else value


def _label(entity_type: str, name: str) -> str:
    return f"{entity_type}:{name}"


def _entity_from_props(props: dict[str, Any]) -> EntityRef:
    return EntityRef(
        type=EntityType(props["type"]),
        name=props["name"],
        aliases=list(props.get("aliases") or []),
        properties={k: v for k, v in props.items() if k not in INTERNAL_ENTITY_KEYS},
        provenance=props.get("provenance", "user"),
        created_at=_iso(props.get("createdAt")),
        updated_at=_iso(props.get("updatedAt")),
        deleted_at=_iso(props.get("deletedAt")),
    )


def _relation_from_record(
    from_label: str, rel_type: str, to_label: str, props: dict[str, Any]
) -> RelationRef:
    return RelationRef(
        from_entity=from_label,
        type=RelationType(rel_type),
        to_entity=to_label,
        properties={k: v for k, v in props.items() if k not in INTERNAL_RELATION_KEYS},
        valid_from=_to_date(props.get("validFrom")),
        valid_to=_to_date(props.get("validTo")),
        provenance=props.get("provenance", "user"),
        confidence=props.get("confidence"),
    )


# ---- context graph (rendered by formatting.py) -------------------------------


@dataclass
class ContextEdge:
    relation: RelationRef
    touches_center: bool


@dataclass
class ContextGraph:
    center: EntityRef
    edges: list[ContextEdge] = field(default_factory=list)
    notes: list[NoteRef] = field(default_factory=list)
    revisions: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False


# ---- repository -------------------------------------------------------------


class KnowledgeGraph:
    def __init__(self, driver: AsyncDriver, database: str = "neo4j") -> None:
        self._driver = driver
        self._database = database

    async def _write(self, fn: Any, *args: Any) -> Any:
        async with self._driver.session(database=self._database) as session:
            return await session.execute_write(fn, *args)

    async def _read(self, fn: Any, *args: Any) -> Any:
        async with self._driver.session(database=self._database) as session:
            return await session.execute_read(fn, *args)

    # -- resolution --

    @staticmethod
    async def _resolve(
        tx: AsyncManagedTransaction,
        ref: str,
        entity_type: EntityType | None = None,
        include_deleted: bool = False,
    ) -> dict[str, Any]:
        """Find one entity by name or alias. `ref` may carry a `Type:` prefix."""
        prefixed_type, name = parse_entity_ref(ref)
        entity_type = entity_type or prefixed_type
        result = await tx.run(
            """
            MATCH (n:Entity)
            WHERE (n.normName = $norm OR $norm IN n.aliasKeys)
              AND ($type IS NULL OR n.type = $type)
              AND ($includeDeleted OR n.deletedAt IS NULL)
            RETURN properties(n) AS props
            """,
            norm=normalize_name(name),
            type=entity_type.value if entity_type else None,
            includeDeleted=include_deleted,
        )
        rows = [r["props"] async for r in result]
        if not rows:
            scope = f" of type {entity_type.value}" if entity_type else ""
            raise EntityNotFound(
                f"No entity{scope} named '{name}'. Use search to find the right name, "
                "or upsert_entity to create it first."
            )
        if len(rows) > 1:
            candidates = ", ".join(sorted(_label(r["type"], r["name"]) for r in rows))
            raise AmbiguousEntity(
                f"'{name}' matches several entities: {candidates}. "
                "Pass the entity type (or use the 'Type:name' form) to pick one."
            )
        return rows[0]

    # -- entities --

    async def upsert_entity(
        self,
        entity_type: EntityType,
        name: str,
        properties: dict[str, Any] | None = None,
        aliases: list[str] | None = None,
        provenance: Provenance = "user",
    ) -> UpsertResult:
        name = name.strip()
        if not normalize_name(name):
            raise InvalidInput("name must not be empty.")
        properties = validate_properties(dict(properties or {}), INTERNAL_ENTITY_KEYS, "property")
        policy.check_text(name, "name")
        aliases = [a.strip() for a in (aliases or []) if normalize_name(a)]
        for alias in aliases:
            policy.check_text(alias, "alias")
        return await self._write(
            self._upsert_entity_tx, entity_type, name, properties, aliases, provenance
        )

    async def _upsert_entity_tx(
        self,
        tx: AsyncManagedTransaction,
        entity_type: EntityType,
        name: str,
        properties: dict[str, Any],
        aliases: list[str],
        provenance: Provenance,
    ) -> UpsertResult:
        try:
            existing = await self._resolve(tx, name, entity_type, include_deleted=True)
        except EntityNotFound:
            existing = None

        own_key = existing["key"] if existing else entity_key(entity_type, name)
        await self._check_alias_conflicts(tx, entity_type, own_key, aliases)

        if existing is None:
            alias_list = _dedupe_aliases(name, aliases)
            props = {k: v for k, v in properties.items() if v is not None}
            result = await tx.run(
                f"""
                CREATE (n:Entity:{entity_type.value})
                SET n = $props,
                    n.key = $key, n.type = $type, n.name = $name, n.normName = $norm,
                    n.aliases = $aliases, n.aliasKeys = $aliasKeys,
                    n.aliasText = $aliasText, n.provenance = $provenance,
                    n.createdAt = datetime(), n.updatedAt = datetime()
                RETURN properties(n) AS props
                """,
                props=props,
                key=own_key,
                type=entity_type.value,
                name=name,
                norm=normalize_name(name),
                aliases=alias_list,
                aliasKeys=[normalize_name(a) for a in alias_list],
                aliasText=" ".join(alias_list),
                provenance=provenance,
            )
            record = await result.single(strict=True)
            return UpsertResult(status="created", entity=_entity_from_props(record["props"]))

        changes = {
            k: {"before": _jsonable(existing.get(k)), "after": _jsonable(v)}
            for k, v in properties.items()
            if existing.get(k) != v
        }
        old_aliases = list(existing.get("aliases") or [])
        new_aliases = _dedupe_aliases(existing["name"], old_aliases + aliases + [name])
        if new_aliases != old_aliases:
            changes["aliases"] = {"before": old_aliases, "after": new_aliases}
        restored = existing.get("deletedAt") is not None
        if restored:
            changes["deletedAt"] = {"before": _iso(existing["deletedAt"]), "after": None}

        if not changes:
            return UpsertResult(status="unchanged", entity=_entity_from_props(existing))

        result = await tx.run(
            """
            MATCH (n:Entity {key: $key})
            SET n += $props,
                n.aliases = $aliases, n.aliasKeys = $aliasKeys, n.aliasText = $aliasText,
                n.updatedAt = datetime(), n.deletedAt = null
            CREATE (rev:Revision {changedAt: datetime(), changes: $changes,
                                  provenance: $provenance})-[:REVISION_OF]->(n)
            RETURN properties(n) AS props
            """,
            key=existing["key"],
            props=properties,
            aliases=new_aliases,
            aliasKeys=[normalize_name(a) for a in new_aliases],
            aliasText=" ".join(new_aliases),
            changes=json.dumps(changes, ensure_ascii=False),
            provenance=provenance,
        )
        record = await result.single(strict=True)
        return UpsertResult(
            status="restored" if restored else "updated",
            entity=_entity_from_props(record["props"]),
            changed=sorted(changes),
        )

    @staticmethod
    async def _check_alias_conflicts(
        tx: AsyncManagedTransaction, entity_type: EntityType, own_key: str, aliases: list[str]
    ) -> None:
        if not aliases:
            return
        result = await tx.run(
            """
            UNWIND $norms AS norm
            MATCH (n:Entity {type: $type})
            WHERE n.key <> $key AND (n.normName = norm OR norm IN n.aliasKeys)
            RETURN DISTINCT n.name AS name
            """,
            norms=[normalize_name(a) for a in aliases],
            type=entity_type.value,
            key=own_key,
        )
        clashes = [r["name"] async for r in result]
        if clashes:
            raise InvalidInput(
                f"Alias already used by another {entity_type.value}: {', '.join(clashes)}. "
                "If they are the same thing, update that entity instead."
            )

    async def list_entities(
        self,
        entity_type: EntityType | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 50,
        include_deleted: bool = False,
    ) -> list[EntityRef]:
        filters = dict(filters or {})
        for key in filters:
            if not _PROPERTY_KEY.match(key):
                raise InvalidInput(f"filter key '{key}' must be an ASCII identifier.")

        async def tx_fn(tx: AsyncManagedTransaction) -> list[EntityRef]:
            result = await tx.run(
                """
                MATCH (n:Entity)
                WHERE ($type IS NULL OR n.type = $type)
                  AND ($includeDeleted OR n.deletedAt IS NULL)
                  AND all(k IN keys($filters) WHERE n[k] = $filters[k])
                RETURN properties(n) AS props
                ORDER BY n.updatedAt DESC
                LIMIT $limit
                """,
                type=entity_type.value if entity_type else None,
                filters=filters,
                limit=limit,
                includeDeleted=include_deleted,
            )
            return [_entity_from_props(r["props"]) async for r in result]

        return await self._read(tx_fn)

    async def delete_entity(
        self,
        name: str,
        confirm: bool,
        hard: bool = False,
        entity_type: EntityType | None = None,
    ) -> DeleteResult:
        async def tx_fn(tx: AsyncManagedTransaction) -> DeleteResult:
            target = await self._resolve(tx, name, entity_type, include_deleted=hard)
            label = _label(target["type"], target["name"])
            counts = await tx.run(
                """
                MATCH (n:Entity {key: $key})
                RETURN COUNT { (n)-[r]-(:Entity) } AS rels,
                       COUNT { (n)<-[:ABOUT]-(:Note) } AS notes
                """,
                key=target["key"],
            )
            row = await counts.single(strict=True)
            rels, notes = row["rels"], row["notes"]

            if not confirm:
                mode = "permanently delete" if hard else "soft-delete (restorable)"
                return DeleteResult(
                    status="needs_confirmation",
                    entity=label,
                    relation_count=rels,
                    note_count=notes,
                    message=(
                        f"This will {mode} {label}, which has {rels} relation(s) and "
                        f"{notes} note(s). Confirm with the user, then call again with "
                        "confirm=true."
                    ),
                )

            if not hard:
                await tx.run(
                    "MATCH (n:Entity {key: $key}) SET n.deletedAt = datetime()",
                    key=target["key"],
                )
                return DeleteResult(
                    status="soft_deleted",
                    entity=label,
                    relation_count=rels,
                    note_count=notes,
                    message=(
                        f"{label} is hidden from search and context. Upserting the same "
                        "name restores it with its relations."
                    ),
                )

            # Hard delete: notes that were only about this entity go with it.
            await tx.run(
                """
                MATCH (n:Entity {key: $key})
                OPTIONAL MATCH (n)<-[:ABOUT]-(note:Note)
                WHERE COUNT { (note)-[:ABOUT]->() } = 1
                OPTIONAL MATCH (n)<-[:REVISION_OF]-(rev:Revision)
                DETACH DELETE note, rev
                WITH DISTINCT n
                DETACH DELETE n
                """,
                key=target["key"],
            )
            return DeleteResult(
                status="hard_deleted",
                entity=label,
                relation_count=rels,
                note_count=notes,
                message=f"{label}, its relations, history and notes about only it are gone.",
            )

        return await self._write(tx_fn)

    # -- relations --

    async def add_relation(
        self,
        from_ref: str,
        relation_type: RelationType,
        to_ref: str,
        properties: dict[str, Any] | None = None,
        from_type: EntityType | None = None,
        to_type: EntityType | None = None,
        valid_from: date | None = None,
        provenance: Provenance = "user",
        confidence: float | None = None,
    ) -> RelationResult:
        properties = validate_properties(
            dict(properties or {}), INTERNAL_RELATION_KEYS, "relation property"
        )
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise InvalidInput("confidence must be between 0 and 1.")
        valid_from = valid_from or date.today()

        async def tx_fn(tx: AsyncManagedTransaction) -> RelationResult:
            src = await self._resolve(tx, from_ref, from_type)
            dst = await self._resolve(tx, to_ref, to_type)
            error = check_relation_rule(
                relation_type, EntityType(src["type"]), EntityType(dst["type"])
            )
            if error:
                raise InvalidInput(error)
            src_label = _label(src["type"], src["name"])
            dst_label = _label(dst["type"], dst["name"])

            current = await self._current_relation(tx, src["key"], relation_type, dst["key"])
            previous: RelationRef | None = None
            if current is not None:
                current_ref = _relation_from_record(
                    src_label, relation_type.value, dst_label, current["props"]
                )
                if current_ref.properties == properties:
                    return RelationResult(status="unchanged", relation=current_ref)
                # Facts change; history stays. Close the old edge, open a new one.
                await tx.run(
                    "MATCH ()-[r]->() WHERE elementId(r) = $id SET r.validTo = $validTo",
                    id=current["id"],
                    validTo=valid_from,
                )
                previous = current_ref.model_copy(update={"valid_to": valid_from})

            result = await tx.run(
                f"""
                MATCH (a:Entity {{key: $from}}), (b:Entity {{key: $to}})
                CREATE (a)-[r:{relation_type.value}]->(b)
                SET r = $props, r.validFrom = $validFrom, r.provenance = $provenance,
                    r.confidence = $confidence, r.createdAt = datetime()
                RETURN properties(r) AS props
                """,
                **{"from": src["key"], "to": dst["key"]},
                props={k: v for k, v in properties.items() if v is not None},
                validFrom=valid_from,
                provenance=provenance,
                confidence=confidence,
            )
            record = await result.single(strict=True)
            relation = _relation_from_record(
                src_label, relation_type.value, dst_label, record["props"]
            )
            return RelationResult(
                status="superseded" if previous else "created",
                relation=relation,
                previous=previous,
            )

        return await self._write(tx_fn)

    async def end_relation(
        self,
        from_ref: str,
        relation_type: RelationType,
        to_ref: str,
        from_type: EntityType | None = None,
        to_type: EntityType | None = None,
        valid_to: date | None = None,
    ) -> RelationResult:
        valid_to = valid_to or date.today()

        async def tx_fn(tx: AsyncManagedTransaction) -> RelationResult:
            src = await self._resolve(tx, from_ref, from_type)
            dst = await self._resolve(tx, to_ref, to_type)
            current = await self._current_relation(tx, src["key"], relation_type, dst["key"])
            src_label = _label(src["type"], src["name"])
            dst_label = _label(dst["type"], dst["name"])
            if current is None:
                raise EntityNotFound(
                    f"No current {relation_type.value} relation from {src_label} to {dst_label}."
                )
            result = await tx.run(
                """
                MATCH ()-[r]->() WHERE elementId(r) = $id
                SET r.validTo = $validTo
                RETURN properties(r) AS props
                """,
                id=current["id"],
                validTo=valid_to,
            )
            record = await result.single(strict=True)
            return RelationResult(
                status="ended",
                relation=_relation_from_record(
                    src_label, relation_type.value, dst_label, record["props"]
                ),
            )

        return await self._write(tx_fn)

    @staticmethod
    async def _current_relation(
        tx: AsyncManagedTransaction, from_key: str, relation_type: RelationType, to_key: str
    ) -> dict[str, Any] | None:
        result = await tx.run(
            f"""
            MATCH (:Entity {{key: $from}})-[r:{relation_type.value}]->(:Entity {{key: $to}})
            WHERE r.validTo IS NULL
            RETURN elementId(r) AS id, properties(r) AS props
            """,
            **{"from": from_key, "to": to_key},
        )
        record = await result.single()
        return dict(record) if record else None

    # -- notes --

    async def add_note(
        self,
        content: str,
        about: list[str],
        source: str = "conversation",
        provenance: Provenance = "user",
    ) -> NoteRef:
        content = content.strip()
        if not content:
            raise InvalidInput("content must not be empty.")
        if not about:
            raise InvalidInput(
                "about_entities must name at least one entity; a note nobody links to "
                "can never be found by get_context."
            )
        policy.check_text(content, "note content")

        async def tx_fn(tx: AsyncManagedTransaction) -> NoteRef:
            targets = [await self._resolve(tx, ref) for ref in about]
            keys = list(dict.fromkeys(t["key"] for t in targets))
            result = await tx.run(
                """
                CREATE (note:Note {id: randomUUID(), content: $content, source: $source,
                                   provenance: $provenance, createdAt: datetime()})
                WITH note
                UNWIND $keys AS key
                MATCH (n:Entity {key: key})
                CREATE (note)-[:ABOUT]->(n)
                RETURN note.id AS id, toString(note.createdAt) AS createdAt,
                       collect(n.type + ':' + n.name) AS about
                """,
                content=content,
                source=source,
                provenance=provenance,
                keys=keys,
            )
            record = await result.single(strict=True)
            return NoteRef(
                id=record["id"],
                content=content,
                source=source,
                provenance=provenance,
                about=record["about"],
                created_at=record["createdAt"],
            )

        return await self._write(tx_fn)

    # -- context --

    async def get_context(
        self,
        ref: str,
        depth: int = 1,
        entity_type: EntityType | None = None,
        include_history: bool = False,
    ) -> ContextGraph:
        if depth not in (1, 2):
            raise InvalidInput("depth must be 1 or 2.")

        async def tx_fn(tx: AsyncManagedTransaction) -> ContextGraph:
            center = await self._resolve(tx, ref, entity_type)
            graph = ContextGraph(center=_entity_from_props(center))

            # Only :Entity nodes are walked, so notes and revisions never leak in here.
            result = await tx.run(
                f"""
                MATCH p = (c:Entity {{key: $key}})-[*1..{depth}]-(:Entity)
                WHERE all(x IN nodes(p) WHERE x:Entity AND x.deletedAt IS NULL)
                  AND all(r IN relationships(p) WHERE $history OR r.validTo IS NULL)
                UNWIND relationships(p) AS r
                WITH DISTINCT r
                LIMIT $limit
                WITH r, startNode(r) AS a, endNode(r) AS b
                RETURN a.type + ':' + a.name AS fromLabel, type(r) AS type,
                       b.type + ':' + b.name AS toLabel, properties(r) AS props,
                       (a.key = $key OR b.key = $key) AS touchesCenter
                """,
                key=center["key"],
                history=include_history,
                limit=MAX_CONTEXT_RELATIONS + 1,
            )
            rows = [r async for r in result]
            graph.truncated = len(rows) > MAX_CONTEXT_RELATIONS
            for row in rows[:MAX_CONTEXT_RELATIONS]:
                graph.edges.append(
                    ContextEdge(
                        relation=_relation_from_record(
                            row["fromLabel"], row["type"], row["toLabel"], row["props"]
                        ),
                        touches_center=row["touchesCenter"],
                    )
                )

            result = await tx.run(
                """
                MATCH (c:Entity {key: $key})<-[:ABOUT]-(note:Note)
                WITH note ORDER BY note.createdAt DESC LIMIT $limit
                MATCH (note)-[:ABOUT]->(n:Entity)
                RETURN note.id AS id, note.content AS content, note.source AS source,
                       note.provenance AS provenance, toString(note.createdAt) AS createdAt,
                       collect(n.type + ':' + n.name) AS about
                ORDER BY createdAt DESC
                """,
                key=center["key"],
                limit=MAX_CONTEXT_NOTES,
            )
            graph.notes = [
                NoteRef(
                    id=r["id"],
                    content=r["content"],
                    source=r["source"],
                    provenance=r["provenance"] or "user",
                    about=r["about"],
                    created_at=r["createdAt"],
                )
                async for r in result
            ]

            if include_history:
                result = await tx.run(
                    """
                    MATCH (c:Entity {key: $key})<-[:REVISION_OF]-(rev:Revision)
                    RETURN toString(rev.changedAt) AS changedAt, rev.changes AS changes
                    ORDER BY rev.changedAt DESC LIMIT 10
                    """,
                    key=center["key"],
                )
                graph.revisions = [
                    {"changed_at": r["changedAt"], "changes": json.loads(r["changes"])}
                    async for r in result
                ]
            return graph

        return await self._read(tx_fn)

    # -- search --

    async def search(
        self,
        query: str,
        types: list[EntityType] | None = None,
        include_notes: bool = True,
        limit: int = 10,
    ) -> list[SearchHit]:
        if not query.strip():
            raise InvalidInput("query must not be empty.")
        lucene = build_fulltext_query(query)
        type_values = [t.value for t in types] if types else None

        async def tx_fn(tx: AsyncManagedTransaction) -> list[SearchHit]:
            hits: dict[str, SearchHit] = {}

            # Exact name/alias and substring matches outrank fuzzy fulltext hits.
            result = await tx.run(
                """
                MATCH (n:Entity)
                WHERE n.deletedAt IS NULL AND ($types IS NULL OR n.type IN $types)
                  AND (n.normName CONTAINS $norm OR any(a IN n.aliasKeys WHERE a CONTAINS $norm))
                RETURN n.type + ':' + n.name AS label, coalesce(n.summary, '') AS snippet,
                       CASE WHEN n.normName = $norm OR $norm IN n.aliasKeys
                            THEN 100.0 ELSE 50.0 END AS score
                LIMIT $limit
                """,
                norm=normalize_name(query),
                types=type_values,
                limit=limit,
            )
            async for r in result:
                hits[r["label"]] = SearchHit(kind="entity", **dict(r))

            result = await tx.run(
                """
                CALL db.index.fulltext.queryNodes('entity_fulltext', $q, {limit: $limit})
                YIELD node, score
                WHERE node.deletedAt IS NULL AND ($types IS NULL OR node.type IN $types)
                RETURN node.type + ':' + node.name AS label,
                       coalesce(node.summary, node.description, node.rationale, '') AS snippet,
                       score
                """,
                q=lucene,
                types=type_values,
                limit=limit,
            )
            async for r in result:
                hits.setdefault(r["label"], SearchHit(kind="entity", **dict(r)))

            if include_notes:
                result = await tx.run(
                    """
                    CALL db.index.fulltext.queryNodes('note_fulltext', $q, {limit: $limit})
                    YIELD node, score
                    WHERE EXISTS { (node)-[:ABOUT]->(n:Entity) WHERE n.deletedAt IS NULL }
                    RETURN node.id AS label, node.content AS snippet, score
                    """,
                    q=lucene,
                    limit=limit,
                )
                async for r in result:
                    hits.setdefault(
                        r["label"],
                        SearchHit(
                            kind="note",
                            label=r["label"],
                            snippet=_truncate(r["snippet"], 200),
                            score=r["score"],
                        ),
                    )

            return sorted(hits.values(), key=lambda h: h.score, reverse=True)[:limit]

        return await self._read(tx_fn)


def _dedupe_aliases(canonical: str, aliases: list[str]) -> list[str]:
    """Keep first spelling of each alias; drop ones equal to the canonical name."""
    seen = {normalize_name(canonical)}
    out: list[str] = []
    for alias in aliases:
        norm = normalize_name(alias)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(alias)
    return out


def _jsonable(value: Any) -> Any:
    return _iso(value) if hasattr(value, "iso_format") else value


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
