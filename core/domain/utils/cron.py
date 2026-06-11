"""
Utilidades de evaluación de cron para el scheduler.

ÚNICA fuente de verdad para computar ocurrencias de una expresión cron.
Todo código que necesite "la próxima ocurrencia de este cron" pasa por acá:
el repo al persistir, el service al recomputar tras una ejecución, y los
reconciliadores de builtins en el container.

Razón de existir: el cron se evaluaba en tres lugares distintos — el repo
en la timezone del usuario, el service y los reconciliadores en UTC. Una
tarea `0 6 * * *` corría a las 6:00 locales la primera vez y a las 6:00 UTC
las siguientes, produciendo ejecuciones dobles separadas por el offset
(2h en CEST). Centralizar la evaluación elimina esa clase de bug.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from core.domain.errors import InvalidScheduleError

logger = logging.getLogger(__name__)


def resolve_timezone(name: str) -> ZoneInfo:
    """Construye ZoneInfo desde un nombre IANA, con fallback a UTC + WARNING."""
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning("Timezone '%s' inválida — fallback a UTC para evaluación de cron", name)
        return ZoneInfo("UTC")


def validate_cron(schedule: str) -> None:
    """Valida la sintaxis de una expresión cron.

    Raises:
        InvalidScheduleError: con mensaje accionable (el LLM lo usa para
            auto-corregirse en el retry del tool loop).
    """
    if not croniter.is_valid(schedule):
        raise InvalidScheduleError(
            f"Invalid cron expression '{schedule}'. Expected 5 fields: "
            "minute hour day month weekday (e.g. '0 8 * * *' = every day at 08:00)."
        )


def next_cron_occurrence(schedule: str, tz: ZoneInfo, after: datetime | None = None) -> datetime:
    """Próxima ocurrencia de ``schedule`` evaluada en ``tz``, devuelta en UTC.

    El cron se interpreta SIEMPRE en la timezone del usuario — `0 6 * * *`
    significa 06:00 hora local, respetando DST. El resultado se convierte a
    UTC, que es la moneda interna de ``next_run`` (el loop compara en UTC).

    Args:
        schedule: Expresión cron de 5 campos.
        tz: Timezone del usuario en la que se interpreta la expresión.
        after: Punto de partida (default: ahora). Cualquier tz-aware sirve;
            se convierte a ``tz`` antes de evaluar.
    """
    base = (after or datetime.now(timezone.utc)).astimezone(tz)
    next_local = croniter(schedule, base).get_next(datetime)
    return next_local.astimezone(timezone.utc)
