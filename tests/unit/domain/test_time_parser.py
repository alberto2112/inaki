"""Unit tests for core.domain.utils.time_parser.parse_schedule."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
        "+5x",  # unknown unit
        "+ 2h",  # space after plus
        "++5h",  # double plus
        "+5h2d",  # wrong order (h before d)
        "+2H",  # uppercase unit letter
        "5h",  # missing leading plus
        "",  # empty string
        "now+5h",  # garbage prefix
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
# ISO 8601 — strings con timezone explícita
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected_utc"),
    [
        ("2026-04-12T14:00:00Z", datetime(2026, 4, 12, 14, 0, 0, tzinfo=_UTC)),
        ("2026-04-12T14:00:00+00:00", datetime(2026, 4, 12, 14, 0, 0, tzinfo=_UTC)),
        ("2026-04-12T14:00:00-03:00", datetime(2026, 4, 12, 17, 0, 0, tzinfo=_UTC)),
        ("2026-04-12T14:00:00+02:00", datetime(2026, 4, 12, 12, 0, 0, tzinfo=_UTC)),
    ],
)
def test_iso8601_aware_returns_utc(raw: str, expected_utc: datetime) -> None:
    result = parse_schedule(raw, user_timezone="UTC")
    assert isinstance(result, datetime)
    assert result.tzinfo is not None
    assert result == expected_utc


# ---------------------------------------------------------------------------
# ISO 8601 naive — se interpreta en user_timezone y se convierte a UTC
# ---------------------------------------------------------------------------


def test_iso8601_naive_localized_to_user_timezone() -> None:
    # Europe/Paris en abril es CEST = UTC+2
    result = parse_schedule("2026-04-20T06:00:00", user_timezone="Europe/Paris")
    expected = datetime(2026, 4, 20, 4, 0, 0, tzinfo=_UTC)  # 06:00 Paris = 04:00 UTC
    assert result == expected
    assert result.tzinfo is not None


def test_iso8601_naive_utc_timezone_unchanged() -> None:
    result = parse_schedule("2026-04-12T14:00:00", user_timezone="UTC")
    expected = datetime(2026, 4, 12, 14, 0, 0, tzinfo=_UTC)
    assert result == expected


def test_iso8601_naive_argentina_timezone() -> None:
    # America/Argentina/Buenos_Aires = UTC-3
    result = parse_schedule("2026-04-12T14:00:00", user_timezone="America/Argentina/Buenos_Aires")
    expected = datetime(2026, 4, 12, 17, 0, 0, tzinfo=_UTC)  # 14:00 BA = 17:00 UTC
    assert result == expected


def test_iso8601_naive_unknown_timezone_raises() -> None:
    with pytest.raises(ValueError, match="Unknown timezone"):
        parse_schedule("2026-04-12T14:00:00", user_timezone="Fake/Timezone")


# ---------------------------------------------------------------------------
# user_timezone param se usa en offsets relativos (no cambia el resultado, UTC base)
# ---------------------------------------------------------------------------


@freeze_time(_FROZEN_STR)
def test_user_timezone_param_accepted_for_relative() -> None:
    result = parse_schedule("+1h", user_timezone="America/Argentina/Buenos_Aires")
    assert result == _FROZEN + timedelta(hours=1)
