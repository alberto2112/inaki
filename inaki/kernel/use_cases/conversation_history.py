"""Administrar el historial de conversación de un agente SIN correr un turno.

Lo que los canales necesitan del historial fuera del turno: persistir un mensaje
que no dispara al LLM (grupos con ventana de delay, fotos, transcripciones),
enriquecer uno ya persistido, listar la vista humana y limpiar. Antes vivía
como seis métodos de ``RunAgentUseCase``; correr el turno y administrar el
rastro son dos responsabilidades, y ``turn_dispatch`` (el routing in-flight)
solo necesita la segunda.
"""

from __future__ import annotations

from inaki.kernel.ports.outbound.history_port import IHistoryStore
from inaki.shared.message import Message, Role


class ConversationHistory:
    def __init__(self, history: IHistoryStore, agent_id: str) -> None:
        self._history = history
        self._agent_id = agent_id

    async def record_user_message(self, content: str, channel: str = "", chat_id: str = "") -> None:
        """Persiste un mensaje ``role=user`` sin invocar al LLM.

        Pensado para flujos de grupo donde múltiples mensajes (de varios
        usuarios o bots vía broadcast) llegan dentro de una ventana de delay y
        se acumulan individualmente. Cuando el delay vence, el turno se ejecuta
        sin ``user_input`` y deriva el "turno actual" del trailing batch.
        """
        await self._history.append(
            self._agent_id,
            Message(role=Role.USER, content=content),
            channel=channel,
            chat_id=chat_id,
        )

    async def record_photo_message(self, content: str, channel: str = "", chat_id: str = "") -> int:
        """Persiste un mensaje ``role=user`` y devuelve el ``history_id`` de la fila.

        El handler de fotos lo necesita para ``ProcessPhotoUseCase.execute()``.
        El contenido es el bloque ``@photo`` de la gramática de attachments.
        """
        row_id = await self._history.append(
            self._agent_id,
            Message(role=Role.USER, content=content),
            channel=channel,
            chat_id=chat_id,
        )
        return row_id or 0

    async def record_assistant_message(
        self, content: str, channel: str = "", chat_id: str = ""
    ) -> None:
        """Persiste una respuesta generada por el sistema (p. ej. una transcripción)
        para que el usuario pueda iterar sobre ella."""
        await self._history.append(
            self._agent_id,
            Message(role=Role.ASSISTANT, content=content),
            channel=channel,
            chat_id=chat_id,
        )

    async def update_message_content(self, message_id: int, new_content: str) -> bool:
        """Reemplaza el contenido de un mensaje persistido manteniendo ``id`` y ``created_at``.

        El handler de fotos enriquece el bloque ``@photo`` con la línea
        ``@analysis`` final sin dejar un segundo ``role=user`` consecutivo.
        """
        return await self._history.update_content(self._agent_id, message_id, new_content)

    async def get_history(self) -> list[Message]:
        """La vista HUMANA del historial activo: sin archivados, sin infused y sin
        el plumbing de tool calls (``role=tool``). Los ``assistant`` con
        tool_calls sí se muestran: su narración es conversación legítima."""
        history = await self._history.load(self._agent_id)
        return [m for m in history if m.role != Role.TOOL]

    async def clear_history(self, channel: str | None = None, chat_id: str | None = None) -> None:
        """Sin ``channel``/``chat_id`` borra TODO el historial y resetea ``agent_state``;
        con ellos, solo los mensajes de ese scope, preservando ``agent_state``."""
        await self._history.clear(self._agent_id, channel=channel, chat_id=chat_id)
