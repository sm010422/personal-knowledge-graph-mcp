"""Graph vocabulary: entity types, relation types, and the models tools return.

Relation types are a closed set on purpose. If an LLM can invent relation types
freely, the graph fills up with near-duplicates (BUILT / MADE / WORKED_ON) and
queries stop finding things. See docs/adr/0004-closed-relation-vocabulary.md.
"""

from __future__ import annotations

import unicodedata
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class EntityType(StrEnum):
    PERSON = "Person"
    PROJECT = "Project"
    TECHNOLOGY = "Technology"
    DECISION = "Decision"
    EVENT = "Event"
    ORGANIZATION = "Organization"


class RelationType(StrEnum):
    BUILT = "BUILT"
    USES = "USES"
    KNOWS = "KNOWS"
    HAS_DECISION = "HAS_DECISION"
    APPLIED_TO = "APPLIED_TO"
    WORKED_AT = "WORKED_AT"
    STUDIED_AT = "STUDIED_AT"
    MEMBER_OF = "MEMBER_OF"
    ATTENDED = "ATTENDED"
    CONCERNS = "CONCERNS"
    COLLABORATED_WITH = "COLLABORATED_WITH"
    RELATED_TO = "RELATED_TO"


_E = EntityType
_ANY = frozenset(EntityType)

# Allowed (from, to) entity types for each relation type.
RELATION_RULES: dict[RelationType, tuple[frozenset[EntityType], frozenset[EntityType]]] = {
    RelationType.BUILT: (frozenset({_E.PERSON}), frozenset({_E.PROJECT})),
    RelationType.USES: (frozenset({_E.PROJECT}), frozenset({_E.TECHNOLOGY})),
    RelationType.KNOWS: (frozenset({_E.PERSON}), frozenset({_E.TECHNOLOGY})),
    RelationType.HAS_DECISION: (frozenset({_E.PROJECT}), frozenset({_E.DECISION})),
    RelationType.APPLIED_TO: (frozenset({_E.PERSON}), frozenset({_E.ORGANIZATION})),
    RelationType.WORKED_AT: (frozenset({_E.PERSON}), frozenset({_E.ORGANIZATION})),
    RelationType.STUDIED_AT: (frozenset({_E.PERSON}), frozenset({_E.ORGANIZATION})),
    RelationType.MEMBER_OF: (frozenset({_E.PERSON}), frozenset({_E.ORGANIZATION})),
    RelationType.ATTENDED: (frozenset({_E.PERSON}), frozenset({_E.EVENT})),
    RelationType.CONCERNS: (
        frozenset({_E.EVENT}),
        frozenset({_E.PROJECT, _E.ORGANIZATION, _E.DECISION}),
    ),
    RelationType.COLLABORATED_WITH: (frozenset({_E.PERSON}), frozenset({_E.PERSON})),
    RelationType.RELATED_TO: (_ANY, _ANY),
}

Provenance = Literal["user", "inferred"]


def normalize_name(name: str) -> str:
    """Identity form of a name: NFKC, case-folded, whitespace collapsed."""
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def entity_key(entity_type: EntityType, name: str) -> str:
    return f"{entity_type.value}:{normalize_name(name)}"


def parse_entity_ref(ref: str) -> tuple[EntityType | None, str]:
    """Split an optional `Type:name` prefix, e.g. "Project:k3s" -> (PROJECT, "k3s").

    A colon that is not preceded by a known type stays part of the name.
    """
    prefix, sep, rest = ref.partition(":")
    if sep:
        for t in EntityType:
            if t.value.casefold() == prefix.strip().casefold():
                return t, rest.strip()
    return None, ref.strip()


def check_relation_rule(
    relation: RelationType, from_type: EntityType, to_type: EntityType
) -> str | None:
    """Return an error message if the relation is not allowed between these types."""
    allowed_from, allowed_to = RELATION_RULES[relation]
    if from_type in allowed_from and to_type in allowed_to:
        return None
    return (
        f"{relation.value} is not allowed from {from_type.value} to {to_type.value}. "
        f"Allowed: ({' | '.join(sorted(t.value for t in allowed_from))}) -> "
        f"({' | '.join(sorted(t.value for t in allowed_to))}). "
        "Use RELATED_TO only if no specific relation type fits."
    )


# ---- models returned by tools -------------------------------------------------


class EntityRef(BaseModel):
    type: EntityType
    name: str
    aliases: list[str] = Field(default_factory=list)
    properties: dict[str, object] = Field(default_factory=dict)
    provenance: Provenance = "user"
    created_at: str | None = None
    updated_at: str | None = None
    deleted_at: str | None = None


class UpsertResult(BaseModel):
    status: Literal["created", "updated", "unchanged", "restored"]
    entity: EntityRef
    changed: list[str] = Field(default_factory=list, description="Property names that changed")


class RelationRef(BaseModel):
    from_entity: str = Field(description="Type:name")
    type: RelationType
    to_entity: str = Field(description="Type:name")
    properties: dict[str, object] = Field(default_factory=dict)
    valid_from: date | None = None
    valid_to: date | None = None
    provenance: Provenance = "user"
    confidence: float | None = None


class RelationResult(BaseModel):
    status: Literal["created", "unchanged", "superseded", "ended"]
    relation: RelationRef
    previous: RelationRef | None = Field(
        default=None, description="The relation that was closed, when status is superseded"
    )


class NoteRef(BaseModel):
    id: str
    content: str
    source: str
    provenance: Provenance = "user"
    about: list[str] = Field(default_factory=list, description="Type:name of linked entities")
    created_at: str | None = None


class SearchHit(BaseModel):
    kind: Literal["entity", "note"]
    label: str = Field(description="Type:name for entities, note id for notes")
    snippet: str
    score: float


class DeleteResult(BaseModel):
    status: Literal["needs_confirmation", "soft_deleted", "hard_deleted"]
    entity: str
    relation_count: int
    note_count: int
    message: str
