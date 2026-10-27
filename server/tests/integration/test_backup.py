"""Export -> wipe -> restore must give back the same graph, types included."""

import gzip
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from neo4j import Driver, GraphDatabase
from testcontainers.community.neo4j import Neo4jContainer

from pkg_mcp.backup import BackupError, export_graph, restore_graph
from pkg_mcp.repository import KnowledgeGraph
from pkg_mcp.schema import EntityType, RelationType

pytestmark = pytest.mark.integration


@pytest.fixture
def sync_driver(neo4j_container: Neo4jContainer) -> Iterator[Driver]:
    drv = GraphDatabase.driver(
        neo4j_container.get_connection_url(),
        auth=(neo4j_container.username, neo4j_container.password),
    )
    yield drv
    drv.close()


def snapshot(driver: Driver) -> tuple[list, list]:
    """Graph contents independent of element ids."""
    nodes = driver.execute_query("MATCH (n) RETURN labels(n) AS l, properties(n) AS p").records
    rels = driver.execute_query(
        "MATCH (a)-[r]->(b) RETURN properties(a) AS a, type(r) AS t, properties(r) AS p, "
        "properties(b) AS b"
    ).records
    key = repr
    return (
        sorted(([sorted(r["l"]), r["p"]] for r in nodes), key=key),
        sorted(([r["a"], r["t"], r["p"], r["b"]] for r in rels), key=key),
    )


async def seed(kg: KnowledgeGraph) -> None:
    await kg.upsert_entity(EntityType.PERSON, "Alex", aliases=["알렉스"])
    await kg.upsert_entity(EntityType.ORGANIZATION, "Acme")
    await kg.upsert_entity(EntityType.PROJECT, "Orbit", {"summary": "궤도 추적"})
    await kg.upsert_entity(EntityType.PROJECT, "Orbit", {"summary": "궤도 추적 v2"})  # Revision
    await kg.upsert_entity(EntityType.TECHNOLOGY, "Neo4j")
    await kg.add_relation("Orbit", RelationType.USES, "Neo4j")
    await kg.add_relation(
        "Alex", RelationType.APPLIED_TO, "Acme", {"status": "document"}, valid_from=date(2026, 9, 1)
    )
    await kg.add_relation(  # supersedes the previous one: closed relation with validTo
        "Alex",
        RelationType.APPLIED_TO,
        "Acme",
        {"status": "interview"},
        valid_from=date(2026, 9, 20),
    )
    await kg.add_note("Alex는 Orbit을 혼자 만들었다", about=["Alex", "Orbit"])
    await kg.delete_entity("Neo4j", confirm=True)  # soft delete keeps deletedAt


async def test_round_trip(kg: KnowledgeGraph, sync_driver: Driver, tmp_path: Path) -> None:
    await seed(kg)
    before = snapshot(sync_driver)
    path = tmp_path / "backup.jsonl.gz"

    exported = export_graph(sync_driver, "neo4j", path)
    sync_driver.execute_query("MATCH (n) DETACH DELETE n")
    restored = restore_graph(sync_driver, "neo4j", path)

    assert exported == restored
    assert restored["nodes"] >= 6 and restored["relationships"] >= 5
    assert snapshot(sync_driver) == before

    # The app still works on top of the restored data, fulltext index included.
    context = await kg.get_context("Acme", include_history=True)
    assert {e.relation.properties["status"] for e in context.edges} == {"document", "interview"}
    hits = await kg.search("궤도")
    assert any(h.label == "Project:Orbit" for h in hits)


async def test_restore_refuses_non_empty_database(
    kg: KnowledgeGraph, sync_driver: Driver, tmp_path: Path
) -> None:
    await kg.upsert_entity(EntityType.PERSON, "Alex")
    path = tmp_path / "backup.jsonl.gz"
    export_graph(sync_driver, "neo4j", path)
    with pytest.raises(BackupError, match="not empty"):
        restore_graph(sync_driver, "neo4j", path)


async def test_truncated_file_is_rejected(
    kg: KnowledgeGraph, sync_driver: Driver, tmp_path: Path
) -> None:
    await kg.upsert_entity(EntityType.PERSON, "Alex")
    path = tmp_path / "backup.jsonl.gz"
    export_graph(sync_driver, "neo4j", path)
    with gzip.open(path, "rt") as f:
        lines = f.readlines()
    with gzip.open(path, "wt") as f:
        f.writelines(lines[:-1])
    sync_driver.execute_query("MATCH (n) DETACH DELETE n")
    with pytest.raises(BackupError, match="truncated"):
        restore_graph(sync_driver, "neo4j", path)
