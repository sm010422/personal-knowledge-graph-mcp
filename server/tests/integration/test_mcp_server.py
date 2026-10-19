"""End-to-end through the MCP protocol with an in-memory client."""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from pydantic import SecretStr
from testcontainers.community.neo4j import Neo4jContainer

from pkg_mcp.config import Settings
from pkg_mcp.server import create_server

pytestmark = pytest.mark.integration

EXPECTED_TOOLS = {
    "upsert_entity",
    "add_relation",
    "end_relation",
    "add_note",
    "get_context",
    "search",
    "list_entities",
    "delete_entity",
}


@pytest.fixture
async def client(neo4j_container: Neo4jContainer, kg):  # kg wipes the database
    settings = Settings(
        neo4j_uri=neo4j_container.get_connection_url(),
        neo4j_user=neo4j_container.username,
        neo4j_password=SecretStr(neo4j_container.password),
    )
    async with Client(create_server(settings)) as c:
        yield c


async def test_tools_are_listed_with_descriptions(client: Client) -> None:
    tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == EXPECTED_TOOLS
    for tool in tools.values():
        assert tool.description and len(tool.description) > 80, tool.name


async def test_record_and_recall_round_trip(client: Client) -> None:
    await client.call_tool("upsert_entity", {"type": "Person", "name": "Alex"})
    await client.call_tool(
        "upsert_entity",
        {"type": "Project", "name": "Orbit", "properties": {"status": "active"}},
    )
    created = await client.call_tool(
        "add_relation",
        {"from_entity": "Alex", "relation_type": "BUILT", "to_entity": "Orbit"},
    )
    assert created.structured_content["status"] == "created"

    context = await client.call_tool("get_context", {"entity_name": "Orbit"})
    text = context.content[0].text
    assert "# Project: Orbit" in text
    assert "Person:Alex -BUILT-> **this**" in text


async def test_domain_errors_reach_the_model(client: Client) -> None:
    with pytest.raises(ToolError, match="upsert_entity to create it first"):
        await client.call_tool("get_context", {"entity_name": "Nobody"})
