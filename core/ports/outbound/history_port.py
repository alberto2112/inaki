from abc import ABC, abstractmethod
from core.domain.entities.message import Message
from core.domain.value_objects.conversation_state import ConversationState


class IHistoryStore(ABC):
    @abstractmethod
    async def append(self, agent_id: str, message: Message) -> None: ...

    @abstractmethod
    async def load(self, agent_id: str) -> list[Message]:
        """Retorna los mensajes del historial (ventana en memoria si está configurada)."""
        ...

    @abstractmethod
    async def load_full(self, agent_id: str) -> list[Message]:
        """Retorna el historial completo activo."""
        ...

    @abstractmethod
    async def load_uninfused(self, agent_id: str) -> list[Message]:
        """
        Retorna los mensajes que aún no han pasado por el extractor de recuerdos
        (flag `infused=0`). Usado por la consolidación para evitar re-extraer
        hechos de mensajes ya procesados que siguen vivos en el buffer por el
        trim (keep_last).
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
    async def clear(self, agent_id: str) -> None:
        """Elimina todo el historial del agente. Usado por el slash `/clear`.

        Debe limpiar tanto los mensajes como el estado conversacional asociado
        (sticky skills/tools), manteniendo ambos en sincronía.
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
