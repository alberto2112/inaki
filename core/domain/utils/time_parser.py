"""
Utilidades de parsing de tiempo para el scheduler.

Funciones puras, sin dependencias externas. Pueden ser usadas tanto por
SchedulerTool (adapter) como por la CLI u otros adaptadores.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Regex: "+2d5h30m", "+5h", "+30m", "+1d", "+1d2h30m"
# Al menos uno de los tres grupos debe estar presente.
_RELATIVE_RE = re.compile(r"^\+(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?$")


def parse_schedule(raw: str, user_timezone: str) -> datetime:
    """
    Parsea una cadena de schedule y devuelve un datetime absoluto en UTC.

    Formatos soportados:
      - Relativo: "+2d5h30m", "+5h", "+30m", "+1d"
        → now(UTC) + timedelta. Zero-duration ("+0m", "+0d0h0m") raises ValueError.
      - ISO 8601 con tz: "2026-04-12T14:00:00-03:00", "2026-04-12T14:00:00Z"
        → convertido a UTC-aware.
      - ISO 8601 sin tz: "2026-04-12T14:00:00"
        → interpretado en user_timezone y convertido a UTC.

    Nota: Los cron strings NO son responsabilidad de esta función. El caller
    (SchedulerTool) discrimina si el schedule es cron ANTES de llamar aquí.

    Args:
        raw: El string de schedule a parsear.
        user_timezone: Timezone del usuario (ej. "Europe/Paris"). Usado para
                       localizar datetimes naive (sin offset explícito).

    Returns:
        datetime UTC-aware absoluto.

    Raises:
        ValueError: Formato inválido, duración zero en relativos, o timezone desconocida.
    """
    m = _RELATIVE_RE.match(raw)
    if m:
        days_str, hours_str, minutes_str = m.groups()

        # La regex puede matchear "+", que no tiene ningún grupo — inválido.
        if days_str is None and hours_str is None and minutes_str is None:
            raise ValueError(
                f"Relative schedule '{raw}' must specify at least one of: d, h, m"
            )

        days = int(days_str) if days_str is not None else 0
        hours = int(hours_str) if hours_str is not None else 0
        minutes = int(minutes_str) if minutes_str is not None else 0

        total_minutes = days * 24 * 60 + hours * 60 + minutes
        if total_minutes == 0:
            raise ValueError(
                f"Relative schedule '{raw}' must have a positive duration (got zero)"
            )

        return datetime.now(timezone.utc) + timedelta(days=days, hours=hours, minutes=minutes)

    # ISO 8601 fallback
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        raise ValueError(
            f"Invalid schedule format '{raw}'. "
            "Use relative ('+2d5h30m', '+5h') or ISO 8601 ('2026-04-12T14:00:00Z')."
        ) from None

    if dt.tzinfo is None:
        # Datetime naive — localizar al timezone del usuario y convertir a UTC
        try:
            tz = ZoneInfo(user_timezone)
        except ZoneInfoNotFoundError:
            raise ValueError(
                f"Unknown timezone '{user_timezone}'. "
                "Use a valid IANA timezone name (e.g. 'Europe/Paris')."
            ) from None
        dt = dt.replace(tzinfo=tz).astimezone(timezone.utc)

    return dt.astimezone(timezone.utc)
