from abc import ABC, abstractmethod
from core.domain.entities.message import Message


class IHistoryStore(ABC):

    @abstractmethod
    async def append(self, agent_id: str, message: Message) -> None: ...

    @abstractmethod
    async def load(self, agent_id: str) -> list[Message]:
        """Retorna los mensajes del historial (ventana en memoria si está configurada)."""
        ...

    @abstractmethod
    async def load_full(self, agent_id: str) -> list[Message]:
        """Retorna el historial completo leyendo desde disco. Usar solo para consolidación."""
        ...

    @abstractmethod
    async def archive(self, agent_id: str) -> str:
        """Mueve el historial activo a /archive. Retorna la ruta del archivo."""
        ...

    @abstractmethod
    async def clear(self, agent_id: str) -> None:
        """Elimina el historial activo (usar tras archivar)."""
        ...
