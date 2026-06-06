"""SendTelegramMessageTool — el LLM manda un mensaje de texto a un chat de Telegram.

A diferencia de ``send_to_telegram`` (que adjunta ficheros al chat ACTUAL del
turno), esta tool envía TEXTO a un ``chat_id`` que el LLM provee explícitamente.
Permite escribirle a otro chat distinto al de la conversación en curso.

El mensaje saliente se persiste en el historial bajo el scope del chat DESTINO
(``channel='telegram'``, ``chat_id=<destino>``) como un mensaje ``ASSISTANT``,
para que quede coherente con la conversación de ese chat — no con la del turno.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.domain.entities.message import Message, Role
from core.ports.outbound.history_port import IHistoryStore
from core.ports.outbound.message_sender_port import IMessageSender
from core.ports.outbound.tool_port import ITool, ToolResult

logger = logging.getLogger(__name__)


class SendTelegramMessageTool(ITool):
    name = "send_telegram_message"
    description = (
        "Send a TEXT message to a specific Telegram chat identified by its "
        "numeric chat_id. Use this to write to a chat OTHER than the current "
        "conversation (e.g. notify another user). Required: 'chat_id' (the "
        "numeric Telegram chat id of the recipient) and 'text' (the message "
        "body). To attach a file to the CURRENT chat use 'send_to_telegram' "
        "instead."
    )
    routing_keywords = (
        "mandar mensaje telegram a otro chat avisar notificar usuario "
        "escribir contactar enviar texto a un contacto reenviar "
        "send telegram message to another chat notify user contact someone "
        "envoyer un message telegram à un autre chat prévenir notifier"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "chat_id": {
                "type": "string",
                "description": (
                    "Numeric Telegram chat id of the recipient (serialized as a "
                    "string, e.g. '123456789')."
                ),
            },
            "text": {
                "type": "string",
                "description": "The message body to send.",
            },
        },
        "required": ["chat_id", "text"],
    }

    def __init__(
        self,
        sender: IMessageSender,
        history: IHistoryStore,
        agent_id: str,
    ) -> None:
        self._sender = sender
        self._history = history
        self._agent_id = agent_id

    async def execute(self, **kwargs: Any) -> ToolResult:
        chat_id = str(kwargs.get("chat_id") or "").strip()
        if not chat_id:
            return self._fail("'chat_id' es requerido y no puede ser vacío.", retryable=False)

        text = str(kwargs.get("text") or "").strip()
        if not text:
            return self._fail("'text' es requerido y no puede ser vacío.", retryable=False)

        try:
            await self._sender.send_message(chat_id=chat_id, text=text)
        except ValueError as exc:
            return self._fail(str(exc), retryable=False)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "send_telegram_message: error enviando a chat_id=%s",
                chat_id,
            )
            return self._fail(f"transport error: {exc}", retryable=True)

        # Persistimos en el scope del chat DESTINO, no en el del turno actual:
        # el texto pertenece a la conversación del destinatario.
        await self._history.append(
            self._agent_id,
            Message(role=Role.ASSISTANT, content=text),
            channel="telegram",
            chat_id=chat_id,
        )

        payload = {"sent": True, "chat_id": chat_id}
        return ToolResult(
            tool_name=self.name,
            output=json.dumps(payload, ensure_ascii=False),
            success=True,
        )

    def _fail(self, message: str, *, retryable: bool) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            output=json.dumps({"success": False, "error": message}, ensure_ascii=False),
            success=False,
            error=message,
            retryable=retryable,
        )
