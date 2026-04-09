"""Exchange calendar write operations (create, update, delete)."""

from typing import Any, Dict

from exchangelib import CalendarItem
from exchangelib.errors import ErrorItemNotFound
from exchangelib.items import SEND_ONLY_TO_ALL, SEND_ONLY_TO_CHANGED
from exchangelib.properties import Mailbox

from adapters.outbound.tools.exchange_calendar.time_utils import parse_iso_datetime


class CalendarWriter:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def create(self, params: Dict, tz: Any) -> Dict[str, Any]:
        try:
            subject = params.get("subject")
            if not subject:
                return {"success": False, "error": "Field 'subject' is required for create"}
            local_tz = self._engine._get_timezone()
            start_date = parse_iso_datetime(params.get("start_date"), local_tz)
            end_date = parse_iso_datetime(params.get("end_date"), local_tz)
            if not start_date or not end_date:
                return {
                    "success": False,
                    "error": "Fields 'start_date' and 'end_date' are required for create",
                }
            if end_date <= start_date:
                return {"success": False, "error": "end_date must be after start_date"}

            calendar_param = params.get("calendar")
            calendar_email = (
                self._engine._resolve_to_email(calendar_param)
                if calendar_param
                else self._engine.config["mail"]
            )
            account, folder = self._engine._account_and_folder_for(calendar_param)
            attendees = params.get("attendees", [])
            attendee_mailboxes = [Mailbox(email_address=e) for e in attendees]
            appointment = CalendarItem(
                account=account,
                folder=folder,
                subject=subject,
                body=params.get("body", ""),
                location=params.get("location", ""),
                start=start_date,
                end=end_date,
                required_attendees=attendee_mailboxes if attendee_mailboxes else None,
            )
            appointment.save(send_meeting_invitations=SEND_ONLY_TO_ALL)
            return {
                "success": True,
                "item_id": appointment.id,
                "changekey": appointment.changekey,
                "subject": subject,
                "date_start": start_date.isoformat(),
                "date_end": end_date.isoformat(),
                "calendar": calendar_param or "default",
                "calendar_email": calendar_email,
                "message": "Appointment created",
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def update(self, params: Dict, tz: Any) -> Dict[str, Any]:
        try:
            item_id = params.get("item_id")
            changekey = params.get("changekey")
            if not item_id or not changekey:
                return {
                    "success": False,
                    "error": "Fields 'item_id' and 'changekey' are required for update",
                }
            account = self._engine._get_own_account()
            appointment = account.calendar.get(id=item_id, changekey=changekey)
            local_tz = self._engine._get_timezone()
            if params.get("subject"):
                appointment.subject = params["subject"]
            if params.get("body"):
                appointment.body = params["body"]
            if params.get("location"):
                appointment.location = params["location"]
            if params.get("start_date"):
                appointment.start = parse_iso_datetime(params["start_date"], local_tz)
            if params.get("end_date"):
                appointment.end = parse_iso_datetime(params["end_date"], local_tz)
            appointment.save(send_meeting_invitations=SEND_ONLY_TO_CHANGED)
            return {
                "success": True,
                "item_id": appointment.id,
                "changekey": appointment.changekey,
                "subject": appointment.subject,
                "date_start": appointment.start.isoformat() if appointment.start else None,
                "date_end": appointment.end.isoformat() if appointment.end else None,
                "message": "Appointment updated",
            }
        except ErrorItemNotFound:
            return {"success": False, "error": "Event not found"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def delete(self, params: Dict, tz: Any) -> Dict[str, Any]:
        item_id = params.get("item_id")
        changekey = params.get("changekey")
        if not item_id:
            return {"success": False, "error": "item_id is required"}
        if not changekey:
            return {"success": False, "error": "changekey is required"}
        try:
            account = self._engine._get_own_account()
            appointment = account.calendar.get(id=item_id, changekey=changekey)
            appointment.delete(send_meeting_invitations=SEND_ONLY_TO_ALL)
            return {"success": True, "item_id": item_id}
        except ErrorItemNotFound:
            return {"success": False, "error": "Event not found"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
