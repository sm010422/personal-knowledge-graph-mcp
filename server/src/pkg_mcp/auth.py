"""Shared-secret Bearer authentication for HTTP mode (Phase 2, step 1).

FastMCP ships a StaticTokenVerifier, but it is documented as test-only: it looks
tokens up in a plain dict. This verifier compares in constant time and accepts
two tokens at once so the secret can be rotated without downtime.
See docs/adr/0007-bearer-token-auth.md.
"""

from __future__ import annotations

import hashlib
import hmac

from fastmcp.server.auth import AccessToken, TokenVerifier

MIN_TOKEN_LENGTH = 32
CLIENT_ID = "personal-kg-owner"


class SharedTokenVerifier(TokenVerifier):
    def __init__(self, tokens: list[str]) -> None:
        super().__init__()
        tokens = [t for t in tokens if t]
        if not tokens:
            raise ValueError("At least one auth token is required in HTTP mode.")
        for token in tokens:
            if len(token) < MIN_TOKEN_LENGTH:
                raise ValueError(
                    f"Auth tokens must be at least {MIN_TOKEN_LENGTH} characters "
                    "(generate one with: openssl rand -hex 32)."
                )
        # Compare digests so length differences do not leak through timing either.
        self._digests = [hashlib.sha256(t.encode()).digest() for t in tokens]

    async def verify_token(self, token: str) -> AccessToken | None:
        digest = hashlib.sha256(token.encode()).digest()
        # Check every stored digest; no early exit on match.
        matched = False
        for expected in self._digests:
            matched |= hmac.compare_digest(digest, expected)
        if not matched:
            return None
        return AccessToken(token=token, client_id=CLIENT_ID, scopes=[])
