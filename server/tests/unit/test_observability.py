import json
import logging

import pytest
import sentry_sdk
from pydantic import SecretStr
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from pkg_mcp.config import Settings
from pkg_mcp.observability import init_sentry, report_unexpected, scrub_event

SECRET = "지원현황-비밀-값"


class CapturingTransport(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.envelopes: list[Envelope] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.envelopes.append(envelope)

    def payload(self) -> str:
        return "\n".join(item.get_bytes().decode() for env in self.envelopes for item in env.items)


def settings(**kw) -> Settings:
    return Settings(neo4j_password=SecretStr("x"), _env_file=None, **kw)  # type: ignore[call-arg]


@pytest.fixture
def transport():
    t = CapturingTransport()
    init_sentry(
        settings(sentry_dsn=SecretStr("https://public@example.invalid/1")), "test", transport=t
    )
    yield t
    sentry_sdk.get_client().close()
    sentry_sdk.init()  # back to a disabled client for the other tests


def test_off_without_dsn():
    assert init_sentry(settings(), "test") is False


def test_event_keeps_location_but_not_data(transport):
    def tool(arguments):
        local_copy = arguments["status"]  # noqa: F841
        raise ValueError(f"constraint violated: {arguments['status']}")

    try:
        tool({"status": SECRET})
    except ValueError as exc:
        report_unexpected(exc)
    sentry_sdk.flush()

    payload = transport.payload()
    assert "ValueError" in payload
    assert "test_observability" in payload  # stack frames survive
    assert SECRET not in payload
    assert "local_copy" not in payload


def test_logs_are_not_sent(transport):
    logging.getLogger("fastmcp").error("tool failed with %s", SECRET)
    sentry_sdk.flush()
    assert SECRET not in transport.payload()


def test_scrub_event_shape():
    event = {
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": SECRET,
                    "stacktrace": {"frames": [{"function": "f", "vars": {"a": SECRET}}]},
                }
            ]
        },
        "request": {"method": "POST", "url": "/mcp", "data": SECRET, "headers": {"a": "b"}},
        "extra": {"x": SECRET},
        "breadcrumbs": {"values": [{"message": SECRET}]},
        "contexts": {"trace": {"trace_id": "t", "data": {"mcp.request.argument.x": SECRET}}},
    }
    scrubbed = scrub_event(event)
    assert SECRET not in json.dumps(scrubbed, ensure_ascii=False)
    assert scrubbed["request"] == {"method": "POST", "url": "/mcp"}
    assert scrubbed["exception"]["values"][0]["type"] == "ValueError"
    assert scrubbed["contexts"]["trace"]["trace_id"] == "t"
