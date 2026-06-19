"""Tests for time parsing/formatting."""

import pytest

from lemontage.engine.timecode import parse_seconds, to_timecode


@pytest.mark.parametrize(
    "value,expected",
    [
        (30, 30.0),
        (1.5, 1.5),
        ("30s", 30.0),
        ("90", 90.0),
        ("1m30s", 90.0),
        ("2m", 120.0),
        ("00:01:30", 90.0),
        ("01:30", 90.0),
        ("00:00:05.5", 5.5),
    ],
)
def test_parse_seconds(value, expected):
    assert parse_seconds(value) == expected


def test_parse_invalid_raises():
    with pytest.raises(ValueError):
        parse_seconds("abc")


def test_to_timecode():
    assert to_timecode(90.5) == "00:01:30.500"
    assert to_timecode(3661.0) == "01:01:01.000"
