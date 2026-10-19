from datetime import date

import pytest

from pkg_mcp.errors import EntityNotFound, InvalidInput
from pkg_mcp.repository import KnowledgeGraph
from pkg_mcp.schema import EntityType, RelationType

pytestmark = pytest.mark.integration


@pytest.fixture
async def seeded(kg: KnowledgeGraph) -> KnowledgeGraph:
    await kg.upsert_entity(EntityType.PERSON, "Alex")
    await kg.upsert_entity(EntityType.ORGANIZATION, "Acme")
    await kg.upsert_entity(EntityType.PROJECT, "Orbit")
    await kg.upsert_entity(EntityType.TECHNOLOGY, "Neo4j")
    return kg


async def test_create_and_idempotent(seeded: KnowledgeGraph) -> None:
    first = await seeded.add_relation("Orbit", RelationType.USES, "Neo4j")
    assert first.status == "created"
    assert first.relation.from_entity == "Project:Orbit"
    assert first.relation.valid_from == date.today()

    again = await seeded.add_relation("Orbit", RelationType.USES, "Neo4j")
    assert again.status == "unchanged"


async def test_changed_properties_supersede_and_keep_history(seeded: KnowledgeGraph) -> None:
    await seeded.add_relation(
        "Alex", RelationType.APPLIED_TO, "Acme", {"status": "document"}, valid_from=date(2026, 9, 1)
    )
    result = await seeded.add_relation(
        "Alex",
        RelationType.APPLIED_TO,
        "Acme",
        {"status": "interview"},
        valid_from=date(2026, 9, 20),
    )
    assert result.status == "superseded"
    assert result.previous is not None
    assert result.previous.properties == {"status": "document"}
    assert result.previous.valid_to == date(2026, 9, 20)

    current = await seeded.get_context("Acme")
    assert [e.relation.properties for e in current.edges] == [{"status": "interview"}]

    history = await seeded.get_context("Acme", include_history=True)
    statuses = sorted(e.relation.properties["status"] for e in history.edges)
    assert statuses == ["document", "interview"]


async def test_relation_rule_enforced(seeded: KnowledgeGraph) -> None:
    with pytest.raises(InvalidInput, match="USES is not allowed from Technology"):
        await seeded.add_relation("Neo4j", RelationType.USES, "Orbit")


async def test_missing_entity_has_actionable_message(seeded: KnowledgeGraph) -> None:
    with pytest.raises(EntityNotFound, match="upsert_entity"):
        await seeded.add_relation("Orbit", RelationType.USES, "Redis")


async def test_end_relation_hides_from_current_context(seeded: KnowledgeGraph) -> None:
    await seeded.add_relation("Alex", RelationType.WORKED_AT, "Acme")
    ended = await seeded.end_relation(
        "Alex", RelationType.WORKED_AT, "Acme", valid_to=date(2026, 10, 1)
    )
    assert ended.status == "ended"
    assert ended.relation.valid_to == date(2026, 10, 1)
    assert (await seeded.get_context("Alex")).edges == []

    with pytest.raises(EntityNotFound, match="No current WORKED_AT"):
        await seeded.end_relation("Alex", RelationType.WORKED_AT, "Acme")


async def test_inferred_relation_keeps_confidence(seeded: KnowledgeGraph) -> None:
    result = await seeded.add_relation(
        "Alex", RelationType.KNOWS, "Neo4j", provenance="inferred", confidence=0.6
    )
    assert result.relation.provenance == "inferred"
    assert result.relation.confidence == 0.6
