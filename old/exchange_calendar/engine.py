"""
Moteur Exchange Calendar — logique d'exécution sans dépendance à BaseSkill.

Extrait de ExchangeCalendarSkill pour dissocier la couche tool de la couche skill.
"""

import logging
import os
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from exchangelib import Account, Credentials, Configuration, DELEGATE
from exchangelib.ewsdatetime import EWSTimeZone
from exchangelib.errors import ErrorAccessDenied, ErrorServerBusy

from tools.exchange_calendar.calendar_env import (
    ensure_exchange_env_loaded,
    parse_exchange_calendars_from_env,
    resolve_calendar_name,
)
from tools.exchange_calendar.time_utils import resolve_exchange_timezone
from tools.exchange_calendar.notifications import publier_messages_ephemeres_debut_tour
from tools.exchange_calendar.tracing import append_exchange_llm_bridge
from tools.exchange_calendar.reader import CalendarReader
from tools.exchange_calendar.writer import CalendarWriter

ensure_exchange_env_loaded()

logger = logging.getLogger(__name__)

# Garde pour n'activer les loggers EWS qu'une seule fois par processus
_ews_debug_configured: bool = False


def _configure_ews_debug_logging() -> None:
    """Active les loggers exchangelib/requests au niveau DEBUG (idempotent)."""
    global _ews_debug_configured
    if _ews_debug_configured:
        return
    for logger_name in ("exchangelib", "requests"):
        logging.getLogger(logger_name).setLevel(logging.DEBUG)
    _ews_debug_configured = True


class ExchangeCalendarEngine:
    """Moteur Exchange Calendar : gestion des comptes et dispatch des opérations."""

    _own_account: Optional[Account] = None
    _delegate_accounts: Dict[str, Account] = {}

    def __init__(self) -> None:
        mail = os.getenv("EXCHANGE_MAIL", os.getenv("EXCHANGE_EMAIL", ""))
        self.config: Dict[str, Any] = {
            "ews_url": os.getenv("EXCHANGE_EWS_URL", ""),
            "username": os.getenv("EXCHANGE_USERNAME", ""),
            "password": os.getenv("EXCHANGE_PASSWORD", ""),
            "mail": mail,
            "default_timezone": os.getenv("EXCHANGE_TIMEZONE", "UTC"),
            "calendars": parse_exchange_calendars_from_env(),
        }
        self.config = {k: v for k, v in self.config.items() if v is not None and v != ""}
        self._reader = CalendarReader(self)
        self._writer = CalendarWriter(self)

    def _validate_config(self) -> None:
        for key in ("username", "password", "mail"):
            if not self.config.get(key):
                raise ValueError(
                    f"Champ obligatoire manquant : {key}. "
                    "Définir EXCHANGE_USERNAME, EXCHANGE_PASSWORD, EXCHANGE_MAIL dans .env"
                )

    def _get_timezone(self) -> ZoneInfo:
        return resolve_exchange_timezone(self.config.get("default_timezone", "UTC"))

    def _build_ews_config(self) -> Configuration:
        credentials = Credentials(
            username=self.config["username"],
            password=self.config["password"],
        )
        kwargs: Dict[str, Any] = {"credentials": credentials}
        if self.config.get("ews_url"):
            kwargs["server"] = self.config["ews_url"]
        return Configuration(**kwargs)

    def _get_own_account(self) -> Account:
        if self._own_account is None:
            cfg = self._build_ews_config()
            self._own_account = Account(
                primary_smtp_address=self.config["mail"],
                config=cfg,
                autodiscover=not self.config.get("ews_url"),
                access_type=DELEGATE,
            )
        return self._own_account

    def _get_account_for_email(self, email: str) -> Account:
        normalized = email.strip().lower()
        own_mail = self.config["mail"].strip().lower()
        if normalized == own_mail:
            return self._get_own_account()
        if normalized not in self._delegate_accounts:
            cfg = self._build_ews_config()
            self._delegate_accounts[normalized] = Account(
                primary_smtp_address=normalized,
                config=cfg,
                autodiscover=not self.config.get("ews_url"),
                access_type=DELEGATE,
            )
        return self._delegate_accounts[normalized]

    def _resolve_to_email(self, calendar_param: str) -> str:
        result = resolve_calendar_name(calendar_param)
        return result["email"] if result.get("found") else calendar_param.strip().lower()

    def _account_and_folder_for(self, calendar_param: Optional[str]):
        if calendar_param:
            account = self._get_account_for_email(self._resolve_to_email(calendar_param))
        else:
            account = self._get_own_account()
        return account, account.calendar

    async def execute(self, **kwargs: Any) -> Any:
        if logging.getLogger().isEnabledFor(logging.DEBUG):
            _configure_ews_debug_logging()
        await publier_messages_ephemeres_debut_tour(kwargs)
        operation = str(kwargs.get("operation", "")).strip().lower()
        result: Any = None
        try:
            if not operation:
                result = {
                    "success": False,
                    "error": "Le paramètre 'operation' est obligatoire (resolve, read, search, create, update, delete)",
                }
            elif operation == "resolve":
                result = self._execute_resolve(kwargs)
            else:
                tz = EWSTimeZone.from_timezone(self._get_timezone())
                if operation == "read":
                    result = await self._reader.read(kwargs, tz)
                elif operation == "search":
                    result = await self._reader.search(kwargs, tz)
                elif operation == "create":
                    result = await self._writer.create(kwargs, tz)
                elif operation == "update":
                    result = await self._writer.update(kwargs, tz)
                elif operation == "delete":
                    result = await self._writer.delete(kwargs, tz)
                else:
                    result = {
                        "success": False,
                        "error": f"Opération inconnue : {operation}. Utilise resolve, read, search, create, update ou delete.",
                    }
        except ErrorAccessDenied as exc:
            result = {"success": False, "error": f"Accès refusé : {exc}"}
        except ErrorServerBusy as exc:
            result = {"success": False, "error": f"Serveur occupé, réessayer plus tard : {exc}"}
        except Exception as exc:
            result = {"success": False, "error": f"Erreur lors de l'exécution : {exc}"}
        finally:
            append_exchange_llm_bridge(operation, kwargs, result)
        return result

    def _execute_resolve(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = str(params.get("calendar", "") or "").strip()
        resolution = resolve_calendar_name(query)
        if resolution.get("found"):
            email = resolution["email"]
            return {
                "success": True,
                "email": email,
                "display": resolution.get("display", ""),
                "message": (
                    f"User request to process for this calendar (calendar={email})."
                    "Follow up with operation=read or operation=search, passing exactly "
                    "this value in the calendar parameter, along with the dates or range "
                    "from the user message."
                ),
            }
        known = resolution.get("known", [])
        candidates = resolution.get("candidates", [])
        return {
            "success": False,
            "error": (
                f"Impossible de résoudre \"{query}\" en calendrier Exchange. "
                + (f"Candidats proches : {candidates}. " if candidates else "")
                + f"Calendriers connus : {known}."
            ),
        }
