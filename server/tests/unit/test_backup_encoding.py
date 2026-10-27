import pytest
from neo4j.time import Date, DateTime, Duration, Time

from pkg_mcp.backup import BackupError, decode_value, encode_value


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        42,
        1.5,
        "한국어 text",
        ["a", "b"],
        Date(2026, 10, 27),
        DateTime(2026, 10, 27, 3, 0, 0, 123456789).replace(tzinfo=None),
        DateTime.from_iso_format("2026-10-27T03:00:00.123456789+00:00"),
        Time(3, 4, 5),
        Duration(days=3, hours=2),
        [Date(2026, 1, 1), Date(2026, 2, 1)],
    ],
)
def test_round_trip_keeps_type_and_value(value: object) -> None:
    decoded = decode_value(encode_value(value))
    assert decoded == value
    assert type(decoded) is type(value)


def test_temporal_values_are_tagged() -> None:
    assert encode_value(Date(2026, 9, 1)) == {"$date": "2026-09-01"}


def test_unknown_types_fail_loudly() -> None:
    with pytest.raises(BackupError):
        encode_value(object())
    with pytest.raises(BackupError):
        decode_value({"$point": "x"})
