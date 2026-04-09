"""ExchangeCalendarTool — Microsoft Exchange calendar (facade over the engine)."""

from __future__ import annotations

import json
from typing import Any

from core.ports.outbound.tool_port import ITool, ToolResult

_engine: Any = None

_BASE_DESCRIPTION = (
    "Microsoft Exchange calendar: meetings, appointments, and scheduling. "
    "Integrates with Outlook to create, read, update, and delete events. "
    "Use it to inspect a day's plan, a colleague's availability, or client details "
    "(names, team, location, phone numbers). "
    "Requires EXCHANGE_USERNAME, EXCHANGE_PASSWORD, EXCHANGE_MAIL (or EXCHANGE_EMAIL) in .env; "
    "optional EXCHANGE_EWS_URL if autodiscover is disabled. "
    "Optional EXCHANGE_CALENDAR_MAILBOX_MAP for alias→email resolution (see project docs). "
    "Operations create requires subject, start_date, and end_date (ISO 8601). "
    "Operations update and delete require item_id and changekey from a prior read/search. "
    "Use operation=resolve first if you do not know a colleague's exact email address."
)


def _get_engine() -> Any:
    global _engine
    if _engine is None:
        from adapters.outbound.tools.exchange_calendar.engine import ExchangeCalendarEngine

        _engine = ExchangeCalendarEngine()
    return _engine


class ExchangeCalendarTool(ITool):
    name = "exchange_calendar"
    parameters_schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "description": (
                    "Required. Use 'resolve' first if you do not know a colleague's exact email."
                ),
                "enum": ["resolve", "read", "search", "create", "update", "delete"],
            },
            "calendar": {
                "type": "string",
                "description": (
                    "Email or alias of the target calendar. For a colleague, call "
                    "operation=resolve with their first name to obtain the exact address."
                ),
            },
            "start_date": {
                "type": "string",
                "description": (
                    "Start datetime ISO 8601 (e.g. 2026-03-21T09:00:00+01:00). "
                    "Required for create. For read/search, optional (defaults to now / range)."
                ),
            },
            "end_date": {
                "type": "string",
                "description": (
                    "End datetime ISO 8601 (e.g. 2026-03-21T10:00:00+01:00). "
                    "Required for create. For read/search, optional (default window if omitted)."
                ),
            },
            "subject": {
                "type": "string",
                "description": "Subject for search/create/update.",
            },
            "body": {
                "type": "string",
                "description": "Body/description for create/update.",
            },
            "location": {
                "type": "string",
                "description": "Location for create/update.",
            },
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of attendee email addresses for create.",
            },
            "item_id": {
                "type": "string",
                "description": "Event id for update/delete (from a prior read or search).",
            },
            "changekey": {
                "type": "string",
                "description": "Change key for update/delete (from a prior read or search).",
            },
        },
        "required": ["operation"],
    }

    def __init__(self) -> None:
        from adapters.outbound.tools.exchange_calendar.calendar_env import (
            format_calendar_parameter_description_suffix,
        )

        suffix = format_calendar_parameter_description_suffix()
        self.description = _BASE_DESCRIPTION + suffix

    def _validate_params(self, operation: str, kwargs: dict[str, Any]) -> str | None:
        if operation == "create":
            if not (kwargs.get("subject") or "").strip():
                return "Operation 'create' requires non-empty 'subject'."
            if not kwargs.get("start_date"):
                return (
                    "Operation 'create' requires 'start_date' in ISO 8601 format "
                    "(e.g. 2026-03-31T09:00:00+01:00)."
                )
            if not kwargs.get("end_date"):
                return (
                    "Operation 'create' requires 'end_date' in ISO 8601 format "
                    "(e.g. 2026-03-31T10:00:00+01:00)."
                )
        if operation == "update":
            if not kwargs.get("item_id") or not kwargs.get("changekey"):
                return "Operation 'update' requires 'item_id' and 'changekey' from a prior read/search."
        if operation == "delete":
            if not kwargs.get("item_id") or not kwargs.get("changekey"):
                return "Operation 'delete' requires 'item_id' and 'changekey' from a prior read/search."
        return None

    @staticmethod
    def _is_failed_result(result: Any) -> bool:
        if not isinstance(result, dict):
            return False
        if result.get("success") is False:
            return True
        if result.get("error") is not None and result.get("success") is not True:
            return True
        return False

    @staticmethod
    def _failure_message(result: dict) -> str:
        return str(result.get("error", "Operation failed"))

    async def execute(self, **kwargs: Any) -> ToolResult:
        operation = str(kwargs.get("operation", "")).strip().lower()

        if err := self._validate_params(operation, kwargs):
            payload = {"success": False, "error": err}
            return ToolResult(
                tool_name=self.name,
                output=json.dumps(payload, ensure_ascii=False),
                success=False,
                error=err,
            )

        result = await _get_engine().execute(**kwargs)
        output = json.dumps(result, ensure_ascii=False, default=str)

        if self._is_failed_result(result):
            msg = self._failure_message(result) if isinstance(result, dict) else "Operation failed"
            return ToolResult(
                tool_name=self.name,
                output=output,
                success=False,
                error=msg,
            )

        return ToolResult(
            tool_name=self.name,
            output=output,
            success=True,
        )
