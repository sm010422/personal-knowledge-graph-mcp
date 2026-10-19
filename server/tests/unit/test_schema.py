import pytest

from pkg_mcp.schema import (
    EntityType,
    RelationType,
    check_relation_rule,
    entity_key,
    normalize_name,
    parse_entity_ref,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Neo4j", "neo4j"),
        ("  Spring   Boot ", "spring boot"),
        ("ＫＡＦＫＡ", "kafka"),  # full-width -> NFKC
        ("코오롱 베니트", "코오롱 베니트"),
    ],
)
def test_normalize_name(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


def test_entity_key_separates_types() -> None:
    assert entity_key(EntityType.PROJECT, "Kafka") != entity_key(EntityType.TECHNOLOGY, "Kafka")


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("Project:k3s", (EntityType.PROJECT, "k3s")),
        ("project: k3s", (EntityType.PROJECT, "k3s")),
        ("k3s", (None, "k3s")),
        ("ADR:0001", (None, "ADR:0001")),  # unknown prefix stays part of the name
    ],
)
def test_parse_entity_ref(ref: str, expected: tuple) -> None:
    assert parse_entity_ref(ref) == expected


def test_relation_rule_allows_valid_pair() -> None:
    assert check_relation_rule(RelationType.USES, EntityType.PROJECT, EntityType.TECHNOLOGY) is None


def test_relation_rule_rejects_reversed_pair_with_hint() -> None:
    error = check_relation_rule(RelationType.USES, EntityType.TECHNOLOGY, EntityType.PROJECT)
    assert error is not None
    assert "Project" in error and "RELATED_TO" in error


def test_related_to_accepts_anything() -> None:
    for a in EntityType:
        for b in EntityType:
            assert check_relation_rule(RelationType.RELATED_TO, a, b) is None
