from datetime import date, timedelta

import pytest
from neo4j import AsyncDriver

from pkg_mcp.errors import EntityNotFound, InvalidInput
from pkg_mcp.repository import KnowledgeGraph
from pkg_mcp.schema import EntityType, RelationType

pytestmark = pytest.mark.integration


async def test_upcoming_events_window_and_order(kg: KnowledgeGraph) -> None:
    today = kg.today()
    await kg.upsert_entity(EntityType.ORGANIZATION, "Acme")

    def day(offset: int) -> str:
        return (today + timedelta(days=offset)).isoformat()

    await kg.upsert_entity(EntityType.EVENT, "Acme 서류 마감", {"date": day(5) + "T23:59"})
    await kg.upsert_entity(EntityType.EVENT, "Acme 과제", {"date": day(2), "kind": "test"})
    await kg.upsert_entity(EntityType.EVENT, "지난 면접", {"date": day(-1)})
    await kg.upsert_entity(EntityType.EVENT, "먼 발표", {"date": day(40)})
    await kg.upsert_entity(EntityType.EVENT, "날짜 없음")
    await kg.add_relation("Acme 과제", RelationType.CONCERNS, "Acme")

    events = await kg.upcoming_events(days=14)
    assert [e.name for e in events] == ["Acme 과제", "Acme 서류 마감"]
    assert events[0].concerns == ["Organization:Acme"]
    assert events[0].properties == {"kind": "test"}
    assert [e.name for e in await kg.upcoming_events(days=60)][-1] == "먼 발표"


async def test_event_date_must_be_iso(kg: KnowledgeGraph) -> None:
    with pytest.raises(InvalidInput):
        await kg.upsert_entity(EntityType.EVENT, "마감", {"date": "11월 15일"})


async def test_merge_moves_relations_notes_and_aliases(kg: KnowledgeGraph) -> None:
    await kg.upsert_entity(EntityType.PERSON, "Alex")
    await kg.upsert_entity(EntityType.ORGANIZATION, "Club")
    await kg.upsert_entity(EntityType.TECHNOLOGY, "Neo4j")
    await kg.upsert_entity(
        EntityType.PROJECT, "자기소개서 작성", {"summary": "old", "owner": "alex"}, ["cover"]
    )
    await kg.upsert_entity(EntityType.PROJECT, "자소서 작성", {"summary": "keep"})
    await kg.add_relation("Alex", RelationType.BUILT, "자기소개서 작성", {"role": "owner"})
    await kg.add_relation("자기소개서 작성", RelationType.USES, "Neo4j")
    await kg.add_relation("자소서 작성", RelationType.USES, "Neo4j")  # duplicate after merge
    await kg.add_relation("자기소개서 작성", RelationType.PART_OF, "Club")
    await kg.end_relation("자기소개서 작성", RelationType.PART_OF, "Club")  # history moves too
    await kg.add_note("a note", ["자기소개서 작성"])

    preview = await kg.merge_entities("자기소개서 작성", "자소서 작성", confirm=False)
    assert preview.status == "needs_confirmation"
    assert preview.conflicting_properties == {"summary": {"target": "keep", "source": "old"}}
    await kg.get_context("자기소개서 작성")  # still there

    result = await kg.merge_entities("자기소개서 작성", "자소서 작성", confirm=True)
    assert (result.relations_moved, result.duplicate_relations_dropped) == (2, 1)
    assert result.notes_moved == 1

    ctx = await kg.get_context("자기소개서 작성", include_history=True)  # resolves by alias
    assert ctx.center.name == "자소서 작성"
    assert ctx.center.properties == {"summary": "keep", "owner": "alex"}
    assert set(ctx.center.aliases) == {"자기소개서 작성", "cover"}
    rels = {(e.relation.from_entity, e.relation.type, e.relation.to_entity) for e in ctx.edges}
    assert rels == {
        ("Person:Alex", RelationType.BUILT, "Project:자소서 작성"),
        ("Project:자소서 작성", RelationType.USES, "Technology:Neo4j"),
        ("Project:자소서 작성", RelationType.PART_OF, "Organization:Club"),
    }
    assert [n.content for n in ctx.notes] == ["a note"]
    assert any("mergedFrom" in r["changes"] for r in ctx.revisions)
    assert len(await kg.list_entities(EntityType.PROJECT, include_deleted=True)) == 1


async def test_merge_rejects_different_types(kg: KnowledgeGraph) -> None:
    await kg.upsert_entity(EntityType.PROJECT, "Orbit")
    await kg.upsert_entity(EntityType.TECHNOLOGY, "Orbit DB")
    with pytest.raises(InvalidInput):
        await kg.merge_entities("Orbit", "Orbit DB", confirm=True)
    with pytest.raises(EntityNotFound):
        await kg.merge_entities("Nobody", "Orbit", confirm=False)


async def test_stale_filter_counts_notes(kg: KnowledgeGraph, driver: AsyncDriver) -> None:
    for name in ("Old", "Noted", "Fresh"):
        await kg.upsert_entity(EntityType.PROJECT, name, {"status": "active"})
    await driver.execute_query(
        "MATCH (n:Entity) WHERE n.name IN ['Old', 'Noted'] "
        "SET n.updatedAt = datetime() - duration({days: 45})"
    )
    await kg.add_note("still working on it", ["Noted"])

    stale = await kg.list_entities(
        EntityType.PROJECT, {"status": "active"}, not_updated_for_days=30
    )
    assert [e.name for e in stale] == ["Old"]
    assert stale[0].last_activity_at is not None
    with pytest.raises(InvalidInput):
        await kg.list_entities(not_updated_for_days=0)


async def test_stats(kg: KnowledgeGraph) -> None:
    await kg.upsert_entity(EntityType.PERSON, "Alex")
    await kg.upsert_entity(EntityType.PROJECT, "Orbit", provenance="inferred")
    await kg.upsert_entity(EntityType.PROJECT, "Gone")
    await kg.add_relation("Alex", RelationType.BUILT, "Orbit")
    await kg.add_relation("Alex", RelationType.BUILT, "Gone")
    await kg.end_relation("Alex", RelationType.BUILT, "Gone")
    await kg.add_note("n", ["Orbit"])
    await kg.delete_entity("Gone", confirm=True)

    stats = await kg.stats()
    assert stats.entities == {"Project": 1, "Person": 1}
    assert stats.relations == {"BUILT": 1}
    assert (stats.ended_relations, stats.notes) == (1, 1)
    assert (stats.inferred_entities, stats.soft_deleted_entities) == (1, 1)


def test_today_uses_display_timezone(driver: AsyncDriver) -> None:
    from zoneinfo import ZoneInfo

    seoul = KnowledgeGraph(driver, tz=ZoneInfo("Asia/Seoul")).today()
    assert abs((seoul - date.today()).days) <= 1
