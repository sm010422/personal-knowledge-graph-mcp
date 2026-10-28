"""One log line per tool call: which tool, how long it took, how it ended.

This is how we see whether clients actually read the graph (get_context, search)
or only write to it. Arguments are never logged: they are the personal data.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastmcp.exceptions import NotFoundError, ToolError, ValidationError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

from pkg_mcp.errors import KnowledgeGraphError

call_logger = logging.getLogger("pkg_mcp.calls")


class ToolCallLog(Middleware):
    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        start = time.perf_counter()
        # tool_error = the model sent something we rejected (bad args, missing entity);
        # error = a bug or infrastructure failure (also reported to Sentry).
        outcome = "error"
        try:
            result = await call_next(context)
            outcome = "ok"
            return result
        except (ValidationError, NotFoundError):
            outcome = "tool_error"
            raise
        except ToolError as exc:
            # FastMCP wraps unexpected exceptions in ToolError too; the cause tells them apart.
            if exc.__cause__ is None or isinstance(exc.__cause__, KnowledgeGraphError):
                outcome = "tool_error"
            raise
        finally:
            call_logger.info(
                json.dumps(
                    {
                        "event": "tool_call",
                        "tool": context.message.name,
                        "outcome": outcome,
                        "ms": round((time.perf_counter() - start) * 1000, 1),
                    }
                )
            )


def configure_logging() -> None:
    """Send pkg_mcp logs to stderr. FastMCP configures only its own logger.

    stderr keeps stdio mode safe: stdout carries the MCP protocol there.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger("pkg_mcp")
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    root.propagate = False
