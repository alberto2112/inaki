"""Unit tests for core.domain.utils.time_parser.parse_schedule."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from core.domain.utils.time_parser import parse_schedule

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC = timezone.utc
_FROZEN_STR = "2026-04-11T12:00:00+00:00"
_FROZEN = datetime(2026, 4, 11, 12, 0, 0, tzinfo=_UTC)


# ---------------------------------------------------------------------------
# Relative offsets — valid
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected_delta"),
    [
        ("+5h", timedelta(hours=5)),
        ("+2d", timedelta(days=2)),
        ("+30m", timedelta(minutes=30)),
        ("+1d2h30m", timedelta(days=1, hours=2, minutes=30)),
        ("+999d", timedelta(days=999)),
        ("+1d0h1m", timedelta(days=1, minutes=1)),
    ],
)
@freeze_time(_FROZEN_STR)
def test_relative_offset_returns_utc_datetime(raw: str, expected_delta: timedelta) -> None:
    result = parse_schedule(raw, user_timezone="UTC")
    assert result == _FROZEN + expected_delta
    assert result.tzinfo is _UTC


# ---------------------------------------------------------------------------
# Zero-duration rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "+0m",
        "+0d",
        "+0h",
        "+0d0h0m",
        "+0d0h",
        "+0h0m",
    ],
)
def test_zero_duration_raises_value_error(raw: str) -> None:
    with pytest.raises(ValueError, match="positive duration"):
        parse_schedule(raw, user_timezone="UTC")


# ---------------------------------------------------------------------------
# Invalid formats
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "+5x",        # unknown unit
        "+ 2h",       # space after plus
        "++5h",       # double plus
        "+5h2d",      # wrong order (h before d)
        "+2H",        # uppercase unit letter
        "5h",         # missing leading plus
        "",           # empty string
        "now+5h",     # garbage prefix
    ],
)
def test_invalid_format_raises_value_error(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_schedule(raw, user_timezone="UTC")


# ---------------------------------------------------------------------------
# Bare '+' is rejected explicitly (all groups None)
# ---------------------------------------------------------------------------


def test_bare_plus_raises_value_error() -> None:
    with pytest.raises(ValueError, match="at least one"):
        parse_schedule("+", user_timezone="UTC")


# ---------------------------------------------------------------------------
# ISO 8601 passthrough
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "2026-04-12T14:00:00Z",
        "2026-04-12T14:00:00+00:00",
        "2026-04-12T14:00:00-03:00",
        "2026-04-12T14:00:00",
    ],
)
def test_iso8601_passthrough_returns_datetime(raw: str) -> None:
    result = parse_schedule(raw, user_timezone="UTC")
    assert isinstance(result, datetime)
    assert result == datetime.fromisoformat(raw)


# ---------------------------------------------------------------------------
# user_timezone param is accepted without error (reserved for future use)
# ---------------------------------------------------------------------------


@freeze_time(_FROZEN_STR)
def test_user_timezone_param_accepted_for_relative() -> None:
    result = parse_schedule("+1h", user_timezone="America/Argentina/Buenos_Aires")
    assert result == _FROZEN + timedelta(hours=1)
