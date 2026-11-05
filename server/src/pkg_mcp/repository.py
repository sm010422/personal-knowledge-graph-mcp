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
from datetime import UTC, date, datetime, tzinfo
from typing import Any

from neo4j import AsyncDriver, AsyncManagedTransaction

from pkg_mcp import policy
from pkg_mcp.errors import AmbiguousEntity, EntityNotFound, InvalidInput
from pkg_mcp.schema import (
    DeleteResult,
    EntityRef,
    EntityType,
    GraphStats,
    MergeResult,
    NoteDeleteResult,
    NoteRef,
    Provenance,
    RelationRef,
    RelationResult,
    RelationType,
    SearchHit,
    UpcomingEvent,
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
# Lucene reads these as operators only in upper case; the analyzer lowercases anyway.
_LUCENE_OPERATORS = {"AND", "OR", "NOT"}
_EVENT_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")

MAX_CONTEXT_RELATIONS = 200
MAX_CONTEXT_NOTES = 20
DEFAULT_CONTEXT_NOTES = 10


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
        if escaped in _LUCENE_OPERATORS:
            escaped = escaped.lower()
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
    note_total: int = 0


# ---- repository -------------------------------------------------------------


class KnowledgeGraph:
    def __init__(self, driver: AsyncDriver, database: str = "neo4j", tz: tzinfo = UTC) -> None:
        self._driver = driver
        self._database = database
        self.tz = tz

    def today(self) -> date:
        """The user's calendar date, not the server's (a KST morning is still UTC yesterday)."""
        return datetime.now(self.tz).date()

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
        event_date = properties.get("date") if entity_type is EntityType.EVENT else None
        if event_date is not None and not _EVENT_DATE.match(str(event_date)):
            raise InvalidInput(
                "Event date must start with YYYY-MM-DD (e.g. '2026-11-15' or "
                "'2026-11-15T23:59') so upcoming_events can find it."
            )
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
        not_updated_for_days: int | None = None,
    ) -> list[EntityRef]:
        filters = dict(filters or {})
        for key in filters:
            if not _PROPERTY_KEY.match(key):
                raise InvalidInput(f"filter key '{key}' must be an ASCII identifier.")
        if not_updated_for_days is not None and not_updated_for_days < 1:
            raise InvalidInput("not_updated_for_days must be at least 1.")

        match = """
            MATCH (n:Entity)
            WHERE ($type IS NULL OR n.type = $type)
              AND ($includeDeleted OR n.deletedAt IS NULL)
              AND all(k IN keys($filters) WHERE n[k] = $filters[k])
        """
        if not_updated_for_days is None:
            query = match + "RETURN properties(n) AS props ORDER BY n.updatedAt DESC LIMIT $limit"
        else:
            # Adding a note or a relation counts as activity, not only property edits.
            query = (
                match
                + """
            WITH n, reduce(last = n.updatedAt, t IN
                     COLLECT { MATCH (n)<-[:ABOUT]-(x:Note) RETURN x.createdAt }
                   + COLLECT { MATCH (n)-[r]-(:Entity) WHERE r.createdAt IS NOT NULL
                               RETURN r.createdAt }
                   | CASE WHEN t > last THEN t ELSE last END) AS last
            WHERE last < datetime() - duration({days: $days})
            RETURN properties(n) AS props, last ORDER BY last ASC LIMIT $limit
            """
            )

        async def tx_fn(tx: AsyncManagedTransaction) -> list[EntityRef]:
            result = await tx.run(
                query,
                type=entity_type.value if entity_type else None,
                filters=filters,
                limit=limit,
                includeDeleted=include_deleted,
                days=not_updated_for_days,
            )
            out = []
            async for r in result:
                entity = _entity_from_props(r["props"])
                if not_updated_for_days is not None:
                    entity.last_activity_at = _iso(r["last"])
                out.append(entity)
            return out

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
        valid_from = valid_from or self.today()

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
        valid_to = valid_to or self.today()

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

    # -- notes by id --

    async def get_note(self, note_id: str) -> NoteRef:
        async def tx_fn(tx: AsyncManagedTransaction) -> NoteRef:
            return await self._note(tx, note_id)

        return await self._read(tx_fn)

    async def delete_note(self, note_id: str, confirm: bool) -> NoteDeleteResult:
        async def tx_fn(tx: AsyncManagedTransaction) -> NoteDeleteResult:
            note = await self._note(tx, note_id)
            preview = note.model_copy(update={"content": _truncate(note.content, 200)})
            if not confirm:
                return NoteDeleteResult(
                    status="needs_confirmation",
                    note=preview,
                    message="This permanently deletes the note. Confirm with the user, "
                    "then call again with confirm=true.",
                )
            await tx.run("MATCH (note:Note {id: $id}) DETACH DELETE note", id=note_id)
            return NoteDeleteResult(status="deleted", note=preview, message="Note deleted.")

        return await self._write(tx_fn)

    @staticmethod
    async def _note(tx: AsyncManagedTransaction, note_id: str) -> NoteRef:
        result = await tx.run(
            """
            MATCH (note:Note {id: $id})
            OPTIONAL MATCH (note)-[:ABOUT]->(n:Entity)
            RETURN note.content AS content, note.source AS source,
                   note.provenance AS provenance, toString(note.createdAt) AS createdAt,
                   collect(n.type + ':' + n.name) AS about
            """,
            id=note_id.strip(),
        )
        record = await result.single()
        if record is None:
            raise EntityNotFound(
                f"No note with id '{note_id}'. Note ids come from get_context or search."
            )
        return NoteRef(
            id=note_id.strip(),
            content=record["content"],
            source=record["source"],
            provenance=record["provenance"] or "user",
            about=record["about"],
            created_at=record["createdAt"],
        )

    # -- events, maintenance --

    async def upcoming_events(self, days: int = 14) -> list[UpcomingEvent]:
        if not 1 <= days <= 366:
            raise InvalidInput("days must be between 1 and 366.")
        start = self.today()
        end = date.fromordinal(start.toordinal() + days)

        async def tx_fn(tx: AsyncManagedTransaction) -> list[UpcomingEvent]:
            # Dates are stored as 'YYYY-MM-DD[...]' strings, so the first ten
            # characters compare correctly as text.
            result = await tx.run(
                """
                MATCH (e:Entity:Event)
                WHERE e.deletedAt IS NULL AND e.date IS NOT NULL
                WITH e, substring(toString(e.date), 0, 10) AS day
                WHERE $start <= day <= $end
                RETURN properties(e) AS props, toString(e.date) AS date,
                       COLLECT { MATCH (e)-[r:CONCERNS]->(x:Entity)
                                 WHERE r.validTo IS NULL AND x.deletedAt IS NULL
                                 RETURN x.type + ':' + x.name } AS concerns
                ORDER BY date
                """,
                start=start.isoformat(),
                end=end.isoformat(),
            )
            out = []
            async for r in result:
                entity = _entity_from_props(r["props"])
                props = {k: v for k, v in entity.properties.items() if k != "date"}
                out.append(
                    UpcomingEvent(
                        name=entity.name, date=r["date"], properties=props, concerns=r["concerns"]
                    )
                )
            return out

        return await self._read(tx_fn)

    async def stats(self) -> GraphStats:
        async def tx_fn(tx: AsyncManagedTransaction) -> GraphStats:
            result = await tx.run(
                """
                MATCH (n:Entity) WHERE n.deletedAt IS NULL
                RETURN n.type AS type, count(*) AS n ORDER BY n DESC
                """
            )
            entities = {r["type"]: r["n"] async for r in result}
            result = await tx.run(
                """
                MATCH (:Entity)-[r]->(:Entity)
                RETURN type(r) AS type, r.validTo IS NULL AS current, count(*) AS n
                ORDER BY n DESC
                """
            )
            relations: dict[str, int] = {}
            ended = 0
            async for r in result:
                if r["current"]:
                    relations[r["type"]] = r["n"]
                else:
                    ended += r["n"]
            result = await tx.run(
                """
                RETURN COUNT { (:Note) } AS notes,
                       COUNT { (n:Entity) WHERE n.deletedAt IS NULL
                                             AND n.provenance = 'inferred' } AS inferred,
                       COUNT { (n:Entity) WHERE n.deletedAt IS NOT NULL } AS deleted
                """
            )
            row = await result.single(strict=True)
            return GraphStats(
                entities=entities,
                relations=relations,
                ended_relations=ended,
                notes=row["notes"],
                inferred_entities=row["inferred"],
                soft_deleted_entities=row["deleted"],
            )

        return await self._read(tx_fn)

    async def merge_entities(self, source_ref: str, target_ref: str, confirm: bool) -> MergeResult:
        async def tx_fn(tx: AsyncManagedTransaction) -> MergeResult:
            src = await self._resolve(tx, source_ref)
            dst = await self._resolve(tx, target_ref)
            src_label = _label(src["type"], src["name"])
            dst_label = _label(dst["type"], dst["name"])
            if src["key"] == dst["key"]:
                raise InvalidInput(f"'{source_ref}' and '{target_ref}' are the same entity.")
            if src["type"] != dst["type"]:
                raise InvalidInput(
                    f"Cannot merge {src_label} into {dst_label}: only entities of the same "
                    "type can be merged."
                )

            src_props = {k: v for k, v in src.items() if k not in INTERNAL_ENTITY_KEYS}
            conflicts = {
                k: {"target": _jsonable(dst[k]), "source": _jsonable(v)}
                for k, v in src_props.items()
                if k in dst and dst[k] != v
            }
            result = await tx.run(
                """
                MATCH (s:Entity {key: $src})
                RETURN COUNT { (s)-[]-(x:Entity) WHERE x.key <> $dst } AS rels,
                       COUNT { (s)<-[:ABOUT]-(:Note) } AS notes
                """,
                src=src["key"],
                dst=dst["key"],
            )
            counts = await result.single(strict=True)

            if not confirm:
                return MergeResult(
                    status="needs_confirmation",
                    source=src_label,
                    target=dst_label,
                    relations_moved=counts["rels"],
                    duplicate_relations_dropped=0,
                    notes_moved=counts["notes"],
                    conflicting_properties=conflicts,
                    message=(
                        f"{src_label} will be folded into {dst_label} and deleted: its "
                        f"{counts['rels']} relation(s) and {counts['notes']} note(s) move over, "
                        "its name becomes an alias, and properties the target lacks are "
                        "copied. Where both have a value the target's wins. Confirm with the "
                        "user, then call again with confirm=true."
                    ),
                )

            moved = dropped = 0
            for rel in RelationType:
                for pattern, create in (
                    (f"(s)-[r:{rel.value}]->(x:Entity)", f"CREATE (d)-[n:{rel.value}]->(x)"),
                    (f"(s)<-[r:{rel.value}]-(x:Entity)", f"CREATE (x)-[n:{rel.value}]->(d)"),
                ):
                    # An edge the target already has (still current) is not copied twice.
                    result = await tx.run(
                        f"""
                        MATCH (s:Entity {{key: $src}}), (d:Entity {{key: $dst}})
                        MATCH {pattern}
                        WHERE x.key <> $dst
                        WITH d, r, x, r.validTo IS NULL AND EXISTS {{
                            MATCH {pattern.replace("(s)", "(d)").replace("r:", "dup:")}
                            WHERE dup.validTo IS NULL
                        }} AS duplicate
                        CALL (d, r, x, duplicate) {{
                            WITH d, r, x, duplicate WHERE NOT duplicate
                            {create}
                            SET n = properties(r)
                        }}
                        DELETE r
                        RETURN sum(CASE WHEN duplicate THEN 0 ELSE 1 END) AS moved,
                               sum(CASE WHEN duplicate THEN 1 ELSE 0 END) AS dropped
                        """,
                        src=src["key"],
                        dst=dst["key"],
                    )
                    row = await result.single(strict=True)
                    moved += row["moved"]
                    dropped += row["dropped"]

            result = await tx.run(
                """
                MATCH (s:Entity {key: $src}), (d:Entity {key: $dst})
                OPTIONAL MATCH (s)<-[a:ABOUT]-(note:Note)
                FOREACH (_ IN CASE WHEN note IS NULL THEN [] ELSE [1] END |
                    MERGE (note)-[:ABOUT]->(d))
                DELETE a
                WITH s, d, count(note) AS notes
                OPTIONAL MATCH (s)<-[h:REVISION_OF]-(rev:Revision)
                FOREACH (_ IN CASE WHEN rev IS NULL THEN [] ELSE [1] END |
                    CREATE (rev)-[:REVISION_OF]->(d))
                DELETE h
                WITH s, notes
                DETACH DELETE s
                RETURN notes
                """,
                src=src["key"],
                dst=dst["key"],
            )
            notes = (await result.single(strict=True))["notes"]

            old_aliases = list(dst.get("aliases") or [])
            new_aliases = _dedupe_aliases(
                dst["name"], [*old_aliases, src["name"], *(src.get("aliases") or [])]
            )
            copied = {k: v for k, v in src_props.items() if k not in dst}
            changes: dict[str, Any] = {
                k: {"before": None, "after": _jsonable(v)} for k, v in copied.items()
            }
            changes["mergedFrom"] = {"before": None, "after": src_label}
            if new_aliases != old_aliases:
                changes["aliases"] = {"before": old_aliases, "after": new_aliases}
            await tx.run(
                """
                MATCH (d:Entity {key: $dst})
                SET d += $props,
                    d.aliases = $aliases, d.aliasKeys = $aliasKeys, d.aliasText = $aliasText,
                    d.updatedAt = datetime()
                CREATE (:Revision {changedAt: datetime(), changes: $changes,
                                   provenance: 'user'})-[:REVISION_OF]->(d)
                """,
                dst=dst["key"],
                props=copied,
                aliases=new_aliases,
                aliasKeys=[normalize_name(a) for a in new_aliases],
                aliasText=" ".join(new_aliases),
                changes=json.dumps(changes, ensure_ascii=False),
            )
            return MergeResult(
                status="merged",
                source=src_label,
                target=dst_label,
                relations_moved=moved,
                duplicate_relations_dropped=dropped,
                notes_moved=notes,
                conflicting_properties=conflicts,
                message=f"{src_label} is now part of {dst_label}.",
            )

        return await self._write(tx_fn)

    # -- context --

    async def get_context(
        self,
        ref: str,
        depth: int = 1,
        entity_type: EntityType | None = None,
        include_history: bool = False,
        note_limit: int = DEFAULT_CONTEXT_NOTES,
    ) -> ContextGraph:
        if depth not in (1, 2):
            raise InvalidInput("depth must be 1 or 2.")
        if not 0 <= note_limit <= MAX_CONTEXT_NOTES:
            raise InvalidInput(f"note_limit must be between 0 and {MAX_CONTEXT_NOTES}.")

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
                limit=note_limit,
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
            result = await tx.run(
                "MATCH (:Entity {key: $key})<-[:ABOUT]-(note:Note) RETURN count(note) AS n",
                key=center["key"],
            )
            graph.note_total = (await result.single(strict=True))["n"]

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
