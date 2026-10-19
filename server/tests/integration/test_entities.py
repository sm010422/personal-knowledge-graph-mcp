import json

import pytest
from neo4j import AsyncDriver

from pkg_mcp.errors import AmbiguousEntity, EntityNotFound, InvalidInput, PolicyViolation
from pkg_mcp.repository import KnowledgeGraph
from pkg_mcp.schema import EntityType

pytestmark = pytest.mark.integration


async def test_create_then_unchanged(kg: KnowledgeGraph) -> None:
    first = await kg.upsert_entity(
        EntityType.PROJECT, "Orbit Tracker", {"status": "active"}, aliases=["오르빗"]
    )
    assert first.status == "created"
    assert first.entity.properties == {"status": "active"}
    assert first.entity.aliases == ["오르빗"]

    again = await kg.upsert_entity(EntityType.PROJECT, "orbit   tracker", {"status": "active"})
    assert again.status == "unchanged"


async def test_update_records_revision(kg: KnowledgeGraph, driver: AsyncDriver) -> None:
    await kg.upsert_entity(EntityType.PROJECT, "Orbit", {"status": "planning", "stars": 1})
    result = await kg.upsert_entity(
        EntityType.PROJECT, "Orbit", {"status": "active", "stars": None}
    )

    assert result.status == "updated"
    assert result.changed == ["stars", "status"]
    assert result.entity.properties == {"status": "active"}  # null removed the property

    records, _, _ = await driver.execute_query(
        "MATCH (:Entity {name: 'Orbit'})<-[:REVISION_OF]-(r:Revision) RETURN r.changes AS c"
    )
    changes = json.loads(records[0]["c"])
    assert changes["status"] == {"before": "planning", "after": "active"}
    assert changes["stars"] == {"before": 1, "after": None}


async def test_alias_resolves_to_same_entity(kg: KnowledgeGraph) -> None:
    await kg.upsert_entity(EntityType.TECHNOLOGY, "Kubernetes", aliases=["k8s", "쿠버네티스"])
    result = await kg.upsert_entity(EntityType.TECHNOLOGY, "쿠버네티스", {"category": "infra"})

    assert result.status == "updated"
    assert result.entity.name == "Kubernetes"
    assert len(await kg.list_entities(EntityType.TECHNOLOGY)) == 1


async def test_new_spelling_becomes_alias(kg: KnowledgeGraph) -> None:
    await kg.upsert_entity(EntityType.TECHNOLOGY, "PostgreSQL")
    result = await kg.upsert_entity(EntityType.TECHNOLOGY, "postgresql", aliases=["Postgres"])
    assert result.entity.aliases == ["Postgres"]


async def test_same_name_different_types_are_distinct(kg: KnowledgeGraph) -> None:
    await kg.upsert_entity(EntityType.TECHNOLOGY, "Kafka")
    await kg.upsert_entity(EntityType.PROJECT, "Kafka")
    with pytest.raises(AmbiguousEntity, match="Project:Kafka, Technology:Kafka"):
        await kg.get_context("Kafka")
    context = await kg.get_context("Technology:Kafka")
    assert context.center.type is EntityType.TECHNOLOGY


async def test_alias_conflict_rejected(kg: KnowledgeGraph) -> None:
    await kg.upsert_entity(EntityType.TECHNOLOGY, "Kubernetes", aliases=["k8s"])
    await kg.upsert_entity(EntityType.TECHNOLOGY, "K3s")
    with pytest.raises(InvalidInput, match="Kubernetes"):
        await kg.upsert_entity(EntityType.TECHNOLOGY, "K3s", aliases=["k8s"])


async def test_policy_blocks_sensitive_property(kg: KnowledgeGraph) -> None:
    with pytest.raises(PolicyViolation):
        await kg.upsert_entity(EntityType.PERSON, "Alex", {"salary": "100"})
    with pytest.raises(EntityNotFound):
        await kg.get_context("Alex")


async def test_list_entities_filters(kg: KnowledgeGraph) -> None:
    await kg.upsert_entity(EntityType.PROJECT, "A", {"status": "active"})
    await kg.upsert_entity(EntityType.PROJECT, "B", {"status": "paused"})
    await kg.upsert_entity(EntityType.TECHNOLOGY, "C", {"status": "active"})

    active = await kg.list_entities(EntityType.PROJECT, {"status": "active"})
    assert [e.name for e in active] == ["A"]
    assert len(await kg.list_entities(None, {"status": "active"})) == 2
