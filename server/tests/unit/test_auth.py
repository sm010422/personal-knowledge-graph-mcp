import pytest

from pkg_mcp.auth import SharedTokenVerifier
from pkg_mcp.config import Settings

TOKEN = "a" * 64
NEXT = "b" * 64


async def test_accepts_configured_token() -> None:
    verifier = SharedTokenVerifier([TOKEN])
    access = await verifier.verify_token(TOKEN)
    assert access is not None
    assert access.client_id == "personal-kg-owner"


async def test_rejects_other_tokens() -> None:
    verifier = SharedTokenVerifier([TOKEN])
    assert await verifier.verify_token("a" * 63) is None
    assert await verifier.verify_token("") is None
    assert await verifier.verify_token(NEXT) is None


async def test_rotation_accepts_both_tokens() -> None:
    verifier = SharedTokenVerifier([TOKEN, NEXT])
    assert await verifier.verify_token(TOKEN) is not None
    assert await verifier.verify_token(NEXT) is not None


@pytest.mark.parametrize("tokens", [[], [""], ["too-short"]])
def test_rejects_missing_or_weak_tokens(tokens: list[str]) -> None:
    with pytest.raises(ValueError):
        SharedTokenVerifier(tokens)


def test_settings_collects_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEO4J_PASSWORD", "x")
    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("MCP_AUTH_TOKEN_NEXT", NEXT)
    assert Settings(_env_file=None).auth_tokens() == [TOKEN, NEXT]  # type: ignore[call-arg]
