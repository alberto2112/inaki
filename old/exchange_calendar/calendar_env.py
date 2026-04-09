"""
Lecture des calendriers Exchange configurés via l'environnement (.env).

Centralisé pour que le moteur et le schéma d'outils LLM utilisent la même source.
"""

import os
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

from tools.exchange_calendar.time_utils import patch_exchangelib_unknown_timezones


def exchange_project_root() -> Path:
    """Racine du dépôt Inaki (parent de tools/)."""
    return Path(__file__).resolve().parent.parent.parent


def ensure_exchange_env_loaded() -> None:
    """Charge .env depuis la racine du projet (prioritaire) puis le cwd."""
    root_env = exchange_project_root() / ".env"
    if root_env.is_file():
        load_dotenv(root_env)
    load_dotenv()
    fallback_tz = os.getenv("EXCHANGE_TIMEZONE", "UTC")
    patch_exchangelib_unknown_timezones(fallback_iana=fallback_tz)


def parse_exchange_calendars_from_env() -> List[Dict[str, Any]]:
    """
    Parse la carte des boîtes calendrier (alias → email SMTP).

    Variable : EXCHANGE_CALENDAR_MAILBOX_MAP

    Format par entrée : « alias1|alias2|alias3:adresse@domaine.com »
    Plusieurs entrées : séparées par des virgules.
    Exemple : « moi|alberto:alberto@soc.fr,jack|jacques:jacques@soc.fr »
    """
    ensure_exchange_env_loaded()
    calendars: List[Dict[str, Any]] = []
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


def resolve_calendar_name(query: str) -> Dict[str, Any]:
    """
    Résout un nom ou alias vers un email Exchange.

    Stratégie de matching (ordre décroissant de priorité) :
    1. Correspondance exacte sur alias ou email
    2. Un seul alias/email commence par le query (préfixe)
    3. Le query est contenu dans un seul alias/email

    Retourne :
    - {"found": True, "email": "...", "display": "..."}  si résolution unique
    - {"found": False, "candidates": [...], "known": [...]}  sinon
    """
    calendars = parse_exchange_calendars_from_env()
    q = (query or "").strip().lower()

    def _display(cal: Dict[str, Any]) -> str:
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
        cal for cal in calendars
        if any(t.startswith(q) for t in ([cal["email"]] + (cal.get("aliases") or [])))
    ]
    if len(prefix_matches) == 1:
        cal = prefix_matches[0]
        return {"found": True, "email": cal["email"], "display": _display(cal)}

    contains_matches = [
        cal for cal in calendars
        if any(q in t for t in ([cal["email"]] + (cal.get("aliases") or [])))
    ]
    if len(contains_matches) == 1:
        cal = contains_matches[0]
        return {"found": True, "email": cal["email"], "display": _display(cal)}

    candidates = [_display(c) for c in (prefix_matches or contains_matches)]
    return {"found": False, "candidates": candidates, "known": known}


def format_calendar_parameter_description_suffix() -> str:
    """Fragment descriptif pour le schéma d'outil LLM."""
    calendars = parse_exchange_calendars_from_env()
    if not calendars:
        return ""

    lignes: List[str] = []
    for cal in calendars:
        email = cal.get("email", "")
        aliases = cal.get("aliases") or []
        if aliases:
            lignes.append(f"{', '.join(aliases)} → {email}")
        elif email:
            lignes.append(email)

    if not lignes:
        return ""

    return (
        " Personnes connues (alias → email) : "
        + "; ".join(lignes)
        + ". Si le nom n'est pas un email connu, appelle d'abord operation=resolve pour obtenir l'email exact."
    )
