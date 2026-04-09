"""
Exchange calendar configuration from environment (.env).

Shared by the engine and optional LLM schema hints.
"""

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from adapters.outbound.tools.exchange_calendar.time_utils import patch_exchangelib_unknown_timezones


def exchange_project_root() -> Path:
    """Repository root (directory containing pyproject.toml)."""
    # adapters/outbound/tools/exchange_calendar/calendar_env.py -> parents[4] = repo root
    return Path(__file__).resolve().parents[4]


def ensure_exchange_env_loaded() -> None:
    """Load .env from project root first, then current working directory."""
    root_env = exchange_project_root() / ".env"
    if root_env.is_file():
        load_dotenv(root_env)
    load_dotenv()
    fallback_tz = os.getenv("EXCHANGE_TIMEZONE", "UTC")
    patch_exchangelib_unknown_timezones(fallback_iana=fallback_tz)


def parse_exchange_calendars_from_env() -> list[dict[str, Any]]:
    """
    Parse mailbox map: alias → SMTP address.

    Env: EXCHANGE_CALENDAR_MAILBOX_MAP
    Format per entry: « alias1|alias2|alias3:address@domain.com », comma-separated entries.
    """
    ensure_exchange_env_loaded()
    calendars: list[dict[str, Any]] = []
    env_cals = os.getenv("EXCHANGE_CALENDAR_MAILBOX_MAP", "") or ""
    if not env_cals or not env_cals.strip():
        return calendars

    for entry in env_cals.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        alias_part, email = entry.split(":", 1)
        alias_part = alias_part.strip()
        email = email.strip().lower()
        if not email:
            continue
        calendars.append(
            {
                "aliases": [a.strip().lower() for a in alias_part.split("|") if a.strip()],
                "email": email,
            }
        )
    return calendars


def resolve_calendar_name(query: str) -> dict[str, Any]:
    """
    Resolve a name or alias to an Exchange email.

    Matching (priority): exact alias/email → single prefix match → single contains match.
    """
    calendars = parse_exchange_calendars_from_env()
    q = (query or "").strip().lower()

    def _display(cal: dict[str, Any]) -> str:
        aliases = cal.get("aliases") or []
        name = aliases[0] if aliases else cal["email"]
        return f"{name} ({cal['email']})"

    if not q:
        if calendars:
            first = calendars[0]
            return {"found": True, "email": first["email"], "display": _display(first)}
        return {"found": False, "candidates": [], "known": []}

    known = [_display(c) for c in calendars]

    for cal in calendars:
        tokens = [cal["email"]] + (cal.get("aliases") or [])
        if q in tokens:
            return {"found": True, "email": cal["email"], "display": _display(cal)}

    prefix_matches = [
        cal
        for cal in calendars
        if any(t.startswith(q) for t in ([cal["email"]] + (cal.get("aliases") or [])))
    ]
    if len(prefix_matches) == 1:
        cal = prefix_matches[0]
        return {"found": True, "email": cal["email"], "display": _display(cal)}

    contains_matches = [
        cal
        for cal in calendars
        if any(q in t for t in ([cal["email"]] + (cal.get("aliases") or [])))
    ]
    if len(contains_matches) == 1:
        cal = contains_matches[0]
        return {"found": True, "email": cal["email"], "display": _display(cal)}

    candidates = [_display(c) for c in (prefix_matches or contains_matches)]
    return {"found": False, "candidates": candidates, "known": known}


def format_calendar_parameter_description_suffix() -> str:
    """Optional fragment for dynamic tool schema extension."""
    calendars = parse_exchange_calendars_from_env()
    if not calendars:
        return ""

    lines: list[str] = []
    for cal in calendars:
        email = cal.get("email", "")
        aliases = cal.get("aliases") or []
        if aliases:
            lines.append(f"{', '.join(aliases)} → {email}")
        elif email:
            lines.append(email)

    if not lines:
        return ""

    return (
        " Known people (alias → email): "
        + "; ".join(lines)
        + ". If the name is not a known email, call operation=resolve first."
    )
