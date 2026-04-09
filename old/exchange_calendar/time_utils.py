"""
Résolution de fuseaux pour Exchange (exchangelib).

exchangelib convertit datetime.timezone via tzname(None) en chaînes du type « UTC+01:00 »,
puis tente ZoneInfo sur cette clé : cela échoue toujours. Il faut donc toujours fournir
un ZoneInfo avec une clé IANA reconnue (Etc/GMT*, ou zones géographiques pour décalages
non entiers).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Décalages en minutes depuis UTC -> zone IANA (CLDR / usage courant)
_FRACTIONAL_OFFSET_MINUTES_TO_IANA: dict[int, str] = {
    -570: "Pacific/Marquesas",  # -09:30
    -210: "America/St_Johns",  # -03:30
    210: "Asia/Tehran",  # +03:30
    270: "Asia/Kabul",  # +04:30
    330: "Asia/Kolkata",  # +05:30
    345: "Asia/Kathmandu",  # +05:45
    390: "Indian/Cocos",  # +06:30
    570: "Australia/Darwin",  # +09:30
    630: "Australia/Lord_Howe",  # +10:30
    765: "Pacific/Chatham",  # +12:45
}


def patch_exchangelib_unknown_timezones(fallback_iana: str = "UTC") -> None:
    """
    Ajoute des entrées manquantes dans MS_TIMEZONE_TO_IANA_MAP d'exchangelib.

    Certains serveurs/clients Exchange émettent des IDs Windows non-standard
    (ex. "Customized Time Zone") qui déclenchent des UserWarning au parsing.
    On les mappe sur la timezone de repli configurée.
    """
    try:
        from exchangelib.winzone import MS_TIMEZONE_TO_IANA_MAP
    except ImportError:
        return

    _UNKNOWN_IDS = ["Customized Time Zone"]
    for tz_id in _UNKNOWN_IDS:
        if tz_id not in MS_TIMEZONE_TO_IANA_MAP:
            MS_TIMEZONE_TO_IANA_MAP[tz_id] = fallback_iana
            logger.debug(
                "exchangelib : fuseau inconnu '%s' mappé sur '%s'", tz_id, fallback_iana
            )


def _normalize_utc_gmt_alias(raw: str) -> str:
    """Normalise UTC+0100 / GMT+1 / espaces vers une forme analysable."""
    s = raw.strip()
    s = re.sub(r"\s+", "", s)
    # UTC+0100 ou GMT-0330 (sans deux-points)
    m = re.match(r"^(UTC|GMT)([+-])(\d{2})(\d{2})$", s, re.IGNORECASE)
    if m:
        return f"{m.group(1).upper()}{m.group(2)}{m.group(3)}:{m.group(4)}"
    # UTC+1 ou GMT+12 (heure seule)
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
    """
    Convertit un décalage fixe en minutes depuis UTC vers une zone IANA
    reconnue par exchangelib (jamais datetime.timezone).
    """
    if offset_minutes in _FRACTIONAL_OFFSET_MINUTES_TO_IANA:
        return ZoneInfo(_FRACTIONAL_OFFSET_MINUTES_TO_IANA[offset_minutes])

    if offset_minutes % 60 != 0:
        return ZoneInfo("UTC")

    h = offset_minutes // 60
    if h == 0:
        return ZoneInfo("UTC")
    if -12 <= h <= 14:
        if h > 0:
            key = f"Etc/GMT-{h}"
        else:
            key = f"Etc/GMT+{-h}"
        try:
            return ZoneInfo(key)
        except Exception:
            pass

    return ZoneInfo("UTC")


def ensure_zoneinfo_aware(dt: datetime, fallback: ZoneInfo) -> datetime:
    """
    Garantit un datetime avec tzinfo = ZoneInfo.

    fromisoformat('...+01:00') produit datetime.timezone : exchangelib casse
    en essayant ZoneInfo('UTC+01:00'). On remappe donc vers IANA.
    """
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
    """
    Retourne toujours un ZoneInfo IANA utilisable par EWSTimeZone.from_timezone.

    Accepte notamment : Europe/Madrid, UTC, UTC+01:00, UTC+0100, GMT-5, etc.
    """
    if raw is None or not str(raw).strip():
        return ZoneInfo("UTC")

    s = _normalize_utc_gmt_alias(str(raw))

    try:
        return ZoneInfo(s)
    except Exception:
        pass

    # UTC±HH:MM ou GMT±HH:MM (après normalisation)
    m = re.match(r"^(?:UTC|GMT)([+-])(\d{1,2}):(\d{2})$", s, re.IGNORECASE)
    if not m:
        return ZoneInfo("UTC")

    sign, hh, mm = m.groups()
    offset_minutes = _offset_minutes_from_match(sign, hh, mm)
    return zoneinfo_from_utc_offset_minutes(offset_minutes)


def parse_iso_datetime(value: "Any", tz: ZoneInfo) -> "Optional[datetime]":
    """Convertit une valeur ISO 8601 en datetime timezone-aware."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_zoneinfo_aware(value, tz)
    if not isinstance(value, str):
        raise ValueError("La date doit être une chaîne ISO 8601")
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    return ensure_zoneinfo_aware(parsed, tz)


def resolve_date_range(
    params: "Dict[str, Any]", tz: ZoneInfo, default_days: int
) -> "Dict[str, datetime]":
    """Calcule la plage de dates depuis les paramètres, avec fallback sur default_days."""
    from datetime import timedelta
    now = datetime.now(tz)
    start = parse_iso_datetime(params.get("start_date"), tz) or now
    end = parse_iso_datetime(params.get("end_date"), tz) or (start + timedelta(days=default_days))
    if end <= start:
        raise ValueError("end_date doit être postérieure à start_date")
    return {"start_date": start, "end_date": end}


def event_to_dict(item: "Any") -> "Dict[str, Optional[str]]":
    """Sérialise un CalendarItem Exchange en dict simple."""
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
