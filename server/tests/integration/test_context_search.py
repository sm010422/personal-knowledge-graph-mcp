import pytest
from neo4j import AsyncDriver

from pkg_mcp.errors import InvalidInput, PolicyViolation
from pkg_mcp.formatting import render_context
from pkg_mcp.repository import KnowledgeGraph
from pkg_mcp.schema import EntityType, RelationType

from .conftest import wait_for_fulltext

pytestmark = pytest.mark.integration


@pytest.fixture
async def graph(kg: KnowledgeGraph) -> KnowledgeGraph:
    await kg.upsert_entity(EntityType.PERSON, "Alex")
    await kg.upsert_entity(
        EntityType.PROJECT, "Orbit", {"status": "active", "summary": "위성 궤도 추적 서비스"}
    )
    await kg.upsert_entity(EntityType.PROJECT, "Ledger", {"status": "paused"})
    await kg.upsert_entity(EntityType.TECHNOLOGY, "Kubernetes", aliases=["쿠버네티스"])
    await kg.add_relation("Alex", RelationType.BUILT, "Orbit", {"role": "owner"})
    await kg.add_relation("Alex", RelationType.BUILT, "Ledger")
    await kg.add_relation("Orbit", RelationType.USES, "Kubernetes")
    await kg.add_relation("Ledger", RelationType.USES, "Kubernetes")
    return kg


async def test_depth_one_and_two(graph: KnowledgeGraph) -> None:
    one = await graph.get_context("Orbit", depth=1)
    assert {(e.relation.from_entity, e.relation.to_entity) for e in one.edges} == {
        ("Person:Alex", "Project:Orbit"),
        ("Project:Orbit", "Technology:Kubernetes"),
    }
    assert all(e.touches_center for e in one.edges)

    two = await graph.get_context("Orbit", depth=2)
    indirect = {
        (e.relation.from_entity, e.relation.to_entity) for e in two.edges if not e.touches_center
    }
    assert indirect == {
        ("Person:Alex", "Project:Ledger"),
        ("Project:Ledger", "Technology:Kubernetes"),
    }


async def test_invalid_depth(graph: KnowledgeGraph) -> None:
    with pytest.raises(InvalidInput):
        await graph.get_context("Orbit", depth=3)


async def test_notes_appear_in_context(graph: KnowledgeGraph) -> None:
    note = await graph.add_note(
        "Neo4j 대신 Postgres도 고민했지만 다단계 탐색 때문에 그래프 DB를 택함",
        ["Orbit", "Technology:Kubernetes"],
    )
    assert sorted(note.about) == ["Project:Orbit", "Technology:Kubernetes"]

    text = render_context(await graph.get_context("Orbit"))
    assert "그래프 DB를 택함" in text
    assert "also about Technology:Kubernetes" in text


async def test_note_requires_entities_and_policy(graph: KnowledgeGraph) -> None:
    with pytest.raises(InvalidInput):
        await graph.add_note("orphan", [])
    with pytest.raises(PolicyViolation):
        await graph.add_note("주민번호 990101-1234567", ["Alex"])


async def test_search_exact_partial_korean_and_notes(
    graph: KnowledgeGraph, driver: AsyncDriver
) -> None:
    await graph.add_note("Grafana 대시보드 구성 완료", ["Orbit"])
    await wait_for_fulltext(driver)

    exact = await graph.search("orbit")
    assert exact[0].label == "Project:Orbit"

    alias = await graph.search("쿠버네티스")
    assert alias[0].label == "Technology:Kubernetes"

    prefix = await graph.search("kube")
    assert "Technology:Kubernetes" in [h.label for h in prefix]

    by_summary = await graph.search("궤도 추적")
    assert "Project:Orbit" in [h.label for h in by_summary]

    notes = await graph.search("대시보드")
    assert any(h.kind == "note" and "Grafana" in h.snippet for h in notes)

    only_tech = await graph.search("orbit", types=[EntityType.TECHNOLOGY])
    assert only_tech == []


async def test_search_survives_lucene_syntax(graph: KnowledgeGraph, driver: AsyncDriver) -> None:
    await wait_for_fulltext(driver)
    assert isinstance(await graph.search('C++ "unbalanced (query'), list)
