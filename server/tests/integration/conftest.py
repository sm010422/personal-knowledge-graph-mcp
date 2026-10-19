"""Neo4j via Testcontainers. One container per test session, wiped between tests."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import AsyncIterator, Iterator

import pytest
from neo4j import AsyncDriver, AsyncGraphDatabase
from testcontainers.community.neo4j import Neo4jContainer

from pkg_mcp.db import bootstrap_schema
from pkg_mcp.repository import KnowledgeGraph

NEO4J_IMAGE = "neo4j:5-community"


def _use_active_docker_context() -> None:
    """Testcontainers only reads DOCKER_HOST. Colima/OrbStack expose their socket
    through a docker context instead, so borrow the active context's endpoint."""
    if os.environ.get("DOCKER_HOST") or not shutil.which("docker"):
        return
    host = subprocess.run(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if host and host != "unix:///var/run/docker.sock":
        os.environ["DOCKER_HOST"] = host
        # Ryuk mounts the socket path as seen *inside* the VM, which is the default one.
        os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")


@pytest.fixture(scope="session")
def neo4j_container() -> Iterator[Neo4jContainer]:
    _use_active_docker_context()
    with Neo4jContainer(NEO4J_IMAGE, password="test-password") as container:
        yield container


@pytest.fixture(scope="session")
async def driver(neo4j_container: Neo4jContainer) -> AsyncIterator[AsyncDriver]:
    drv = AsyncGraphDatabase.driver(
        neo4j_container.get_connection_url(),
        auth=(neo4j_container.username, neo4j_container.password),
    )
    await bootstrap_schema(drv, "neo4j")
    yield drv
    await drv.close()


@pytest.fixture
async def kg(driver: AsyncDriver) -> AsyncIterator[KnowledgeGraph]:
    await driver.execute_query("MATCH (n) DETACH DELETE n")
    yield KnowledgeGraph(driver, "neo4j")


async def wait_for_fulltext(driver: AsyncDriver) -> None:
    """Fulltext indexes update asynchronously after commit."""
    await driver.execute_query("CALL db.awaitIndexes(30)")
