import pytest
from neo4j import AsyncDriver

from pkg_mcp.errors import EntityNotFound
from pkg_mcp.repository import KnowledgeGraph
from pkg_mcp.schema import EntityType, RelationType

pytestmark = pytest.mark.integration


@pytest.fixture
async def graph(kg: KnowledgeGraph) -> KnowledgeGraph:
    await kg.upsert_entity(EntityType.PROJECT, "Orbit")
    await kg.upsert_entity(EntityType.TECHNOLOGY, "Neo4j")
    await kg.add_relation("Orbit", RelationType.USES, "Neo4j")
    await kg.add_note("only about orbit", ["Orbit"])
    await kg.add_note("about both", ["Orbit", "Neo4j"])
    return kg


async def test_preview_without_confirm_changes_nothing(graph: KnowledgeGraph) -> None:
    preview = await graph.delete_entity("Orbit", confirm=False)
    assert preview.status == "needs_confirmation"
    assert (preview.relation_count, preview.note_count) == (1, 2)
    assert (await graph.get_context("Orbit")).center.name == "Orbit"


async def test_soft_delete_then_restore(graph: KnowledgeGraph) -> None:
    result = await graph.delete_entity("Orbit", confirm=True)
    assert result.status == "soft_deleted"
    with pytest.raises(EntityNotFound):
        await graph.get_context("Orbit")
    assert (await graph.get_context("Neo4j")).edges == []  # hidden from neighbors too

    restored = await graph.upsert_entity(EntityType.PROJECT, "Orbit")
    assert restored.status == "restored"
    assert len((await graph.get_context("Orbit")).edges) == 1


async def test_hard_delete_removes_exclusive_notes_only(
    graph: KnowledgeGraph, driver: AsyncDriver
) -> None:
    await graph.upsert_entity(EntityType.PROJECT, "Orbit", {"status": "x"})  # creates a Revision
    result = await graph.delete_entity("Orbit", confirm=True, hard=True)
    assert result.status == "hard_deleted"

    records, _, _ = await driver.execute_query(
        "MATCH (n) RETURN labels(n)[0] AS label, n.content AS content ORDER BY label"
    )
    remaining = [(r["label"], r["content"]) for r in records]
    assert remaining == [("Entity", None), ("Note", "about both")]
