"""
Opérations de lecture du calendrier Exchange (read, search).
"""

from typing import Any, Dict

from exchangelib.ewsdatetime import EWSTimeZone
from exchangelib.errors import ErrorItemNotFound

from tools.exchange_calendar.time_utils import resolve_date_range, event_to_dict


class CalendarReader:
    """Opérations de lecture Exchange : read et search."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def read(self, params: Dict, tz: EWSTimeZone) -> Dict[str, Any]:
        """Lit les événements sur une plage de dates."""
        try:
            local_tz = self._engine._get_timezone()
            date_range = resolve_date_range(params, local_tz, default_days=30)
            account, folder = self._engine._account_and_folder_for(params.get("calendar"))
            items = folder.view(start=date_range["start_date"], end=date_range["end_date"])
            events = [event_to_dict(item) for item in items]
            return {
                "success": True,
                "count": len(events),
                "events": events,
                "calendar": params.get("calendar") or "default",
            }
        except ErrorItemNotFound:
            return {"success": False, "count": 0, "events": [], "error": "Calendrier introuvable"}
        except ValueError as exc:
            return {"success": False, "count": 0, "events": [], "error": str(exc)}
        except Exception as exc:
            return {"success": False, "count": 0, "events": [], "error": str(exc)}

    async def search(self, params: Dict, tz: EWSTimeZone) -> Dict[str, Any]:
        """Recherche des événements par sujet sur une plage de dates."""
        try:
            subject = str(params.get("subject", "") or "").strip()
            local_tz = self._engine._get_timezone()
            date_range = resolve_date_range(params, local_tz, default_days=365)
            account, folder = self._engine._account_and_folder_for(params.get("calendar"))
            items = folder.view(start=date_range["start_date"], end=date_range["end_date"])
            events = []
            for item in items:
                current_subject = str(getattr(item, "subject", "") or "")
                if not subject or subject.lower() in current_subject.lower():
                    events.append(event_to_dict(item))
            return {
                "success": True,
                "count": len(events),
                "events": events,
                "search_term": subject or None,
                "calendar": params.get("calendar") or "default",
            }
        except ErrorItemNotFound:
            return {"success": False, "count": 0, "events": [], "error": "Calendrier introuvable"}
        except ValueError as exc:
            return {"success": False, "count": 0, "events": [], "error": str(exc)}
        except Exception as exc:
            return {"success": False, "count": 0, "events": [], "error": str(exc)}
