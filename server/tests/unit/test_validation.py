import pytest

from pkg_mcp.errors import InvalidInput, PolicyViolation
from pkg_mcp.policy import check_properties, check_text
from pkg_mcp.repository import INTERNAL_ENTITY_KEYS, build_fulltext_query, validate_properties


def test_valid_properties_pass() -> None:
    props = {"status": "active", "stars": 3, "tags": ["a", "b"], "archived": None}
    assert validate_properties(props, INTERNAL_ENTITY_KEYS, "property") == props


@pytest.mark.parametrize(
    "props",
    [
        {"repo url": "x"},  # not an identifier
        {"name": "x"},  # reserved
        {"meta": {"nested": 1}},  # nested object
        {"mixed": [1, "a"]},  # heterogeneous list
    ],
)
def test_invalid_properties_rejected(props: dict) -> None:
    with pytest.raises(InvalidInput):
        validate_properties(props, INTERNAL_ENTITY_KEYS, "property")


@pytest.mark.parametrize("key", ["password", "apiToken", "salary", "healthNote", "주민번호"])
def test_sensitive_keys_rejected(key: str) -> None:
    with pytest.raises(PolicyViolation):
        check_properties({key: "x"})


@pytest.mark.parametrize(
    "text",
    ["내 번호는 990101-1234567", "card 1234 5678 9012 3456"],
)
def test_sensitive_values_rejected(text: str) -> None:
    with pytest.raises(PolicyViolation):
        check_text(text, "note")


def test_ordinary_numbers_allowed() -> None:
    check_text("k3s v1.36.2, 3 nodes, NodePort 30747, 2026-10-19", "note")


def test_fulltext_query_escapes_and_prefixes() -> None:
    assert build_fulltext_query("kube") == "kube OR kube*"
    assert build_fulltext_query("c++") == r"c\+\+"
    assert build_fulltext_query("쿠버네티스") == "쿠버네티스"
