"""Error reporting to Sentry. Off unless SENTRY_DSN is set.

The graph holds personal data, so an event says only *where* the code failed:
exception types, stack frames (file, function, line) and a few tags. Tool
arguments, exception messages, local variables, request bodies, headers, log
lines and breadcrumbs never leave the process.

Expected failures (validation, "call upsert_entity first", database starting up)
are ToolErrors for the model to read and are not reported. Only exceptions the
code did not anticipate reach Sentry.
"""

from __future__ import annotations

from typing import Any

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.mcp import MCPIntegration

from pkg_mcp.config import Settings

# Request fields that identify the endpoint without carrying content.
_REQUEST_KEEP = ("method", "url")


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Strip everything that could contain personal data. Used as before_send."""
    for exc in (event.get("exception") or {}).get("values") or []:
        # Messages can quote values: Neo4j constraint errors include the property.
        exc.pop("value", None)
        for frame in (exc.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)
            # Source lines are our own code, but a line can hold a literal; drop them too.
            for key in ("pre_context", "context_line", "post_context"):
                frame.pop(key, None)

    request = event.get("request")
    if isinstance(request, dict):
        event["request"] = {k: request[k] for k in _REQUEST_KEEP if k in request}

    for key in ("extra", "breadcrumbs", "logentry", "message", "user", "spans"):
        event.pop(key, None)

    trace = (event.get("contexts") or {}).get("trace")
    if isinstance(trace, dict):
        # Span data is where the MCP integration would put tool arguments.
        trace.pop("data", None)
    return event


def init_sentry(settings: Settings, component: str, **overrides: Any) -> bool:
    """Start the SDK if a DSN is configured. Returns whether reporting is on.

    `overrides` go straight to sentry_sdk.init; tests use it to swap the transport.
    """
    if settings.sentry_dsn is None:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=settings.sentry_environment,
        # Collect as little as the SDK allows; scrub_event removes the rest.
        data_collection={
            "user_info": False,
            "cookies": {"mode": "off"},
            "http_headers": {"request": {"mode": "off"}},
            "http_bodies": [],
            "url_query_params": {"mode": "off"},
            "gen_ai": {"inputs": False, "outputs": False},
            "database_query_data": False,
            "stack_frame_variables": False,
            "frame_context_lines": 0,
        },
        max_breadcrumbs=0,
        # Errors only. Tracing would ship spans, and the MCP integration writes
        # every tool argument into span data unconditionally.
        traces_sample_rate=None,
        disabled_integrations=[MCPIntegration()],
        # Log records neither become events nor breadcrumbs: FastMCP logs can echo arguments.
        integrations=[LoggingIntegration(level=None, event_level=None)],
        before_send=scrub_event,
        **overrides,
    )
    sentry_sdk.set_tag("component", component)
    return True


def report_unexpected(exc: BaseException) -> None:
    """Send an exception the code did not anticipate. No-op when Sentry is off."""
    sentry_sdk.capture_exception(exc)
