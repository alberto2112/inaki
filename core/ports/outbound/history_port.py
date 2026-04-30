from abc import ABC, abstractmethod
from core.domain.entities.message import Message
from core.domain.value_objects.conversation_state import ConversationState


class IHistoryStore(ABC):
    @abstractmethod
    async def append(
        self,
        agent_id: str,
        message: Message,
        channel: str = "",
        chat_id: str = "",
    ) -> int | None:
        """
        Persiste un mensaje en el historial del agente.

        Args:
            agent_id: Identificador del agente propietario del historial.
            message: Mensaje a persistir (solo ``Role.USER`` y ``Role.ASSISTANT``).
            channel: Canal de origen del mensaje (ej: ``"telegram"``, ``"cli"``).
                     Cadena vacía cuando el canal no aplica o no es relevante.
            chat_id: Identificador del chat dentro del canal (ej: ID de grupo Telegram).
                     Cadena vacía para chats privados o canales sin distinción de chat.

        Returns:
            El ID autoincremental de la fila insertada, o ``None`` si el rol no
            se persiste (p. ej. tool_call). Útil para obtener el ``history_id``
            necesario al vincular metadata de fotos.
        """
        ...

    @abstractmethod
    async def load(
        self,
        agent_id: str,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[Message]:
        """
        Retorna los mensajes del historial (ventana en memoria si está configurada).

        Args:
            agent_id: Identificador del agente.
            channel: Si no es ``None``, filtra por canal exacto.
            chat_id: Si no es ``None``, filtra por chat_id exacto.
        """
        ...

    @abstractmethod
    async def load_full(self, agent_id: str) -> list[Message]:
        """Retorna el historial completo activo."""
        ...

    @abstractmethod
    async def load_uninfused(
        self,
        agent_id: str,
        channels: list[str] | None = None,
    ) -> list[Message]:
        """
        Retorna los mensajes que aún no han pasado por el extractor de recuerdos
        (flag ``infused=0``). Usado por la consolidación para evitar re-extraer
        hechos de mensajes ya procesados que siguen vivos en el buffer por el
        trim (keep_last).

        Args:
            agent_id: Identificador del agente.
            channels: Si es una lista no vacía, solo retorna mensajes cuyos
                ``channel`` estén en esa lista. ``None`` o lista vacía → sin filtro.
        """
        ...

    @abstractmethod
    async def mark_infused(self, agent_id: str) -> int:
        """
        Marca todos los mensajes del agente con `infused=1`. Retorna el número
        de filas afectadas. Se llama tras una extracción exitosa, antes del
        trim, para que las siguientes corridas no vuelvan a procesar las
        mismas filas.
        """
        ...

    @abstractmethod
    async def trim(self, agent_id: str, keep_last: int) -> None:
        """
        Borra todos los mensajes del agente salvo los N más recientes.

        Se llama tras una consolidación exitosa: los recuerdos relevantes ya
        están extraídos al storage vectorial, pero preservamos los últimos N
        mensajes como contexto inmediato para el próximo turno.

        Si `keep_last <= 0` no borra nada (no-op defensivo).
        Si el agente tiene menos mensajes que `keep_last`, tampoco borra.
        """
        ...

    @abstractmethod
    async def clear(
        self,
        agent_id: str,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        """Elimina historial del agente.

        Si tanto ``channel`` como ``chat_id`` son ``None`` borra TODO el historial
        del agente y también el ``agent_state`` (sticky skills/tools), manteniendo
        ambos en sincronía. Es el modo "limpieza total" (``/clear_all`` en Telegram,
        ``DELETE /history`` en REST, ``/clear`` en CLI).

        Si se proveen ``channel`` y/o ``chat_id`` borra SOLO los mensajes que
        matchean ese filtro y deja el ``agent_state`` intacto (es per-agente, no
        per-chat). Es el modo "limpieza scoped" (``/clear`` en Telegram).
        """
        ...

    @abstractmethod
    async def load_state(self, agent_id: str) -> ConversationState:
        """Retorna el estado conversacional persistido del agente.

        Si no existe estado previo (primer turno o tras ``clear``), devuelve un
        ``ConversationState`` vacío. Nunca retorna ``None``.
        """
        ...

    @abstractmethod
    async def save_state(self, agent_id: str, state: ConversationState) -> None:
        """Persiste el estado conversacional del agente (upsert)."""
        ...
