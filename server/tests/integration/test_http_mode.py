"""Run the real `pkg-mcp` entry point in HTTP mode and talk to it over the network."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest
from fastmcp import Client
from testcontainers.community.neo4j import Neo4jContainer

pytestmark = pytest.mark.integration

TOKEN = "integration-test-token-" + "x" * 32


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str, headers: dict[str, str] | None = None, body: bytes | None = None) -> int:
    request = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except urllib.error.URLError:
        return 0  # not listening yet


@pytest.fixture(scope="module")
def http_server(neo4j_container: Neo4jContainer) -> Iterator[str]:
    port = _free_port()
    env = {
        **os.environ,
        "NEO4J_URI": neo4j_container.get_connection_url(),
        "NEO4J_USER": neo4j_container.username,
        "NEO4J_PASSWORD": neo4j_container.password,
        "MCP_TRANSPORT": "http",
        "MCP_HOST": "0.0.0.0",  # same as in the cluster; loopback would enable Host checks
        "MCP_PORT": str(port),
        "MCP_AUTH_TOKEN": TOKEN,
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "pkg_mcp.server"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while _get(f"{base}/ready") != 200:
            if proc.poll() is not None:
                raise RuntimeError(proc.stderr.read().decode() if proc.stderr else "exited")
            if time.monotonic() > deadline:
                raise TimeoutError("server did not become ready")
            time.sleep(0.3)
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_probes_need_no_token(http_server: str) -> None:
    assert _get(f"{http_server}/health") == 200
    assert _get(f"{http_server}/ready") == 200


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong-token"}, {"Authorization": f"Basic {TOKEN}"}],
)
def test_mcp_endpoint_rejects_missing_or_bad_token(http_server: str, headers: dict) -> None:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
    status = _get(
        f"{http_server}/mcp",
        headers={
            **headers,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        body=body,
    )
    assert status == 401


async def test_client_with_token_can_call_tools(http_server: str, kg) -> None:
    async with Client(f"{http_server}/mcp", auth=TOKEN) as client:
        tools = {t.name for t in await client.list_tools()}
        assert "get_context" in tools
        await client.call_tool("upsert_entity", {"type": "Project", "name": "Remote"})
        result = await client.call_tool("get_context", {"entity_name": "Remote"})
        assert "# Project: Remote" in result.content[0].text


async def test_public_host_header_is_accepted(http_server: str) -> None:
    """Behind Traefik/Funnel the Host header is the public name, not 127.0.0.1."""
    status = _get(f"{http_server}/health", headers={"Host": "k3s-master.taildcdcee.ts.net"})
    assert status == 200
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
    status = _get(
        f"{http_server}/mcp",
        headers={
            "Host": "k3s-master.taildcdcee.ts.net",
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        body=body,
    )
    assert status != 421, "Host validation would block traffic arriving through the ingress"
