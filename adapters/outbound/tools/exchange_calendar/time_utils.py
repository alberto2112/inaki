"""
Timezone helpers for Exchange (exchangelib).

exchangelib maps datetimes via tzname; fixed offsets must use IANA ZoneInfo.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_FRACTIONAL_OFFSET_MINUTES_TO_IANA: dict[int, str] = {
    -570: "Pacific/Marquesas",
    -210: "America/St_Johns",
    210: "Asia/Tehran",
    270: "Asia/Kabul",
    330: "Asia/Kolkata",
    345: "Asia/Kathmandu",
    390: "Indian/Cocos",
    570: "Australia/Darwin",
    630: "Australia/Lord_Howe",
    765: "Pacific/Chatham",
}


def patch_exchangelib_unknown_timezones(fallback_iana: str = "UTC") -> None:
    """Register missing Windows timezone ids in exchangelib's map."""
    try:
        from exchangelib.winzone import MS_TIMEZONE_TO_IANA_MAP
    except ImportError:
        return

    unknown_ids = ["Customized Time Zone"]
    for tz_id in unknown_ids:
        if tz_id not in MS_TIMEZONE_TO_IANA_MAP:
            MS_TIMEZONE_TO_IANA_MAP[tz_id] = fallback_iana
            logger.debug("exchangelib: unknown zone '%s' mapped to '%s'", tz_id, fallback_iana)


def _normalize_utc_gmt_alias(raw: str) -> str:
    s = raw.strip()
    s = re.sub(r"\s+", "", s)
    m = re.match(r"^(UTC|GMT)([+-])(\d{2})(\d{2})$", s, re.IGNORECASE)
    if m:
        return f"{m.group(1).upper()}{m.group(2)}{m.group(3)}:{m.group(4)}"
    m2 = re.match(r"^(UTC|GMT)([+-])(\d{1,2})$", s, re.IGNORECASE)
    if m2:
        h = int(m2.group(3))
        return f"{m2.group(1).upper()}{m2.group(2)}{h:02d}:00"
    return s


def _offset_minutes_from_match(sign: str, hour_str: str, minute_str: str) -> int:
    hours = int(hour_str)
    minutes = int(minute_str)
    total = hours * 60 + minutes
    if sign == "-":
        total = -total
    return total


def zoneinfo_from_utc_offset_minutes(offset_minutes: int) -> ZoneInfo:
    if offset_minutes in _FRACTIONAL_OFFSET_MINUTES_TO_IANA:
        return ZoneInfo(_FRACTIONAL_OFFSET_MINUTES_TO_IANA[offset_minutes])

    if offset_minutes % 60 != 0:
        return ZoneInfo("UTC")

    h = offset_minutes // 60
    if h == 0:
        return ZoneInfo("UTC")
    if -12 <= h <= 14:
        key = f"Etc/GMT-{h}" if h > 0 else f"Etc/GMT+{-h}"
        try:
            return ZoneInfo(key)
        except Exception:
            pass

    return ZoneInfo("UTC")


def ensure_zoneinfo_aware(dt: datetime, fallback: ZoneInfo) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=fallback)

    if isinstance(dt.tzinfo, ZoneInfo):
        return dt

    if isinstance(dt.tzinfo, timezone):
        off = dt.tzinfo.utcoffset(None)
        if off is None:
            return dt.replace(tzinfo=fallback)
        minutes = int(off.total_seconds() // 60)
        return dt.replace(tzinfo=zoneinfo_from_utc_offset_minutes(minutes))

    return dt.replace(tzinfo=fallback)


def resolve_exchange_timezone(raw: Optional[str]) -> ZoneInfo:
    if raw is None or not str(raw).strip():
        return ZoneInfo("UTC")

    s = _normalize_utc_gmt_alias(str(raw))

    try:
        return ZoneInfo(s)
    except Exception:
        pass

    m = re.match(r"^(?:UTC|GMT)([+-])(\d{1,2}):(\d{2})$", s, re.IGNORECASE)
    if not m:
        return ZoneInfo("UTC")

    sign, hh, mm = m.groups()
    offset_minutes = _offset_minutes_from_match(sign, hh, mm)
    return zoneinfo_from_utc_offset_minutes(offset_minutes)


def parse_iso_datetime(value: Any, tz: ZoneInfo) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_zoneinfo_aware(value, tz)
    if not isinstance(value, str):
        raise ValueError("Date must be an ISO 8601 string")
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    return ensure_zoneinfo_aware(parsed, tz)


def resolve_date_range(
    params: Dict[str, Any], tz: ZoneInfo, default_days: int
) -> Dict[str, datetime]:
    from datetime import timedelta

    now = datetime.now(tz)
    start = parse_iso_datetime(params.get("start_date"), tz) or now
    end = parse_iso_datetime(params.get("end_date"), tz) or (start + timedelta(days=default_days))
    if end <= start:
        raise ValueError("end_date must be after start_date")
    return {"start_date": start, "end_date": end}


def event_to_dict(item: Any) -> Dict[str, Optional[str]]:
    description = str(getattr(item, "body", "") or "")
    if len(description) > 500:
        description = f"{description[:500]}..."
    start = getattr(item, "start", None)
    end = getattr(item, "end", None)
    return {
        "subject": getattr(item, "subject", None),
        "date_start": start.isoformat() if start else None,
        "date_end": end.isoformat() if end else None,
        "description": description or None,
        "item_id": getattr(item, "id", None),
        "changekey": getattr(item, "changekey", None),
    }
