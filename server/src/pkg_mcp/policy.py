"""Best-effort guard for the "no sensitive data" policy (docs/security.md).

This cannot catch everything an LLM might write. It blocks the obvious cases —
property keys that name a sensitive category, and values shaped like national
ID or card numbers — so the policy is enforced in code, not only in prose.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from pkg_mcp.errors import PolicyViolation

# Matched as substrings of the case-folded property key.
SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "ssn",
    "rrn",
    "주민",
    "passport",
    "여권",
    "account_number",
    "계좌",
    "card_number",
    "카드번호",
    "salary",
    "연봉",
    "income",
    "소득",
    "diagnosis",
    "진단",
    "병명",
    "medical",
    "health",
    "건강",
)

_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Korean resident registration number", re.compile(r"\b\d{6}-?[1-8]\d{6}\b")),
    ("payment card number", re.compile(r"\b(?:\d{4}[- ]?){3}\d{4}\b")),
)


def check_text(text: str, where: str) -> None:
    for label, pattern in _VALUE_PATTERNS:
        if pattern.search(text):
            raise PolicyViolation(
                f"{where} looks like it contains a {label}. "
                "Sensitive personal data must not be stored (see docs/security.md)."
            )


def check_properties(properties: Mapping[str, object], where: str = "property") -> None:
    for key, value in properties.items():
        folded = key.casefold()
        for fragment in SENSITIVE_KEY_FRAGMENTS:
            if fragment in folded:
                raise PolicyViolation(
                    f"{where} '{key}' names a sensitive category ('{fragment}'). "
                    "Health, finance, credentials and ID numbers are not stored "
                    "(see docs/security.md)."
                )
        for text in _strings(value):
            check_text(text, f"{where} '{key}'")


def _strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                yield item
