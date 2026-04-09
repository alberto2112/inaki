"""
Tool : opérations sur le calendrier Microsoft Exchange.

Façade atomique exposant tools/exchange_calendar/engine.py au LLM.
"""

from typing import Any

TOOL_NAME = "exchange_calendar"
TOOL_VERSION = "1.1.0"
TOOL_ENABLED = True

TOOL_DESCRIPTION = (
    "Microsoft Exchange calendar management, meetings, appointments, agenda planning, and scheduling. "
    "Integrates with Outlook to create, read, update and delete calendar events. "
    "It can be used to see what a user is doing on a specific day, or where a colleague is located at a certain time or date. "
    "It contains information about the day's work plan. "
    "It can be used to look up customer information, such as names, team members, appointment locations, and phone numbers."
    "Next operations requires 'start_date' parameter in ISO 8601 format (e.g., 2026-03-31T09:00:00+01:00): create, update, delete."
)

TOOL_PARAMETERS = [
    {
        "name": "operation",
        "type": "string",
        "description": "Required. Use 'resolve' first if you don't know a colleague's exact email address.",
        "required": True,
        "enum": ["resolve", "read", "search", "create", "update", "delete"],
    },
    {
        "name": "calendar",
        "type": "string",
        "description": (
            "Email or alias of the target calendar. "
            "For colleagues, call operation=resolve with their first name to get the exact email address to use here."
        ),
        "required": False,
    },
    {
        "name": "start_date",
        "type": "string",
        "format": "date-time",
        "description": "Start date ISO 8601 (ex: 2026-03-21T09:00:00+01:00). Required for create, update, and delete operations.",
        "required": False,
    },
    {
        "name": "end_date",
        "type": "string",
        "format": "date-time",
        "description": "End date ISO 8601 (ex: 2026-03-21T10:00:00+01:00)",
        "required": False,
    },
    {
        "name": "subject",
        "type": "string",
        "description": "Subject for search/create/update",
        "required": False,
    },
    {
        "name": "body",
        "type": "string",
        "description": "Description for create/update",
        "required": False,
    },
    {
        "name": "location",
        "type": "string",
        "description": "Location for create/update",
        "required": False,
    },
    {
        "name": "attendees",
        "type": "array",
        "description": "List of email addresses of participants for create",
        "required": False,
    },
    {
        "name": "item_id",
        "type": "string",
        "description": "Event identifier for update and delete. Obtain from a prior read or search call.",
        "required": False,
    },
    {
        "name": "changekey",
        "type": "string",
        "description": "Change key for update and delete. Obtain from a prior read or search call.",
        "required": False,
    },
]

_engine = None


def _get_engine():
    """Retourne le moteur (singleton par processus)."""
    global _engine
    if _engine is None:
        from tools.exchange_calendar.engine import ExchangeCalendarEngine

        _engine = ExchangeCalendarEngine()
    return _engine


async def run(**kwargs: Any) -> Any:
    """Point d'entrée de la tool : délègue au moteur Exchange."""
    operation = kwargs.get("operation", "")
    if operation in ("create", "update", "delete"):
        if not kwargs.get("start_date"):
            return {
                "error": f"Operation '{operation}' requires 'start_date' parameter in ISO 8601 format (e.g., 2026-03-31T09:00:00+01:00)"
            }
    return await _get_engine().execute(**kwargs)
