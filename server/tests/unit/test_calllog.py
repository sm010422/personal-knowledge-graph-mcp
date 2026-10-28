import json
import logging

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from pkg_mcp.calllog import ToolCallLog

SECRET = "지원회사-비밀"


@pytest.fixture
def server() -> FastMCP:
    mcp = FastMCP("t")
    mcp.add_middleware(ToolCallLog())

    @mcp.tool
    def ok(value: str) -> str:
        return "done"

    @mcp.tool
    def rejected(value: str) -> str:
        raise ToolError(f"no such entity {value}")

    @mcp.tool
    def broken(value: str) -> str:
        raise RuntimeError(value)

    @mcp.tool
    def typed(count: int) -> int:
        return count

    return mcp


def logged(caplog) -> list[dict]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "pkg_mcp.calls"]


async def test_one_line_per_call_with_outcome(server, caplog):
    caplog.set_level(logging.INFO, logger="pkg_mcp.calls")
    async with Client(server) as client:
        await client.call_tool("ok", {"value": SECRET})
        for name, args in (
            ("rejected", {"value": SECRET}),
            ("broken", {"value": SECRET}),
            ("typed", {"count": "not a number"}),
        ):
            with pytest.raises(ToolError):
                await client.call_tool(name, args)

    lines = logged(caplog)
    assert [(line["tool"], line["outcome"]) for line in lines] == [
        ("ok", "ok"),
        ("rejected", "tool_error"),
        ("broken", "error"),
        ("typed", "tool_error"),
    ]
    assert all(line["ms"] >= 0 for line in lines)
    # FastMCP's own logger prints the traceback of unexpected errors; ours never has args.
    assert not any(SECRET in r.getMessage() for r in caplog.records if r.name == "pkg_mcp.calls")
