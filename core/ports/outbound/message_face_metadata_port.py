"""Port para el repositorio de metadata de caras por mensaje (message_face_metadata).

El adaptador concreto (MessageFaceMetadataRepo) implementa este port.
La side-table vive en history.db con ON DELETE CASCADE.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.domain.entities.face import MessageFaceMetadata


class IMessageFaceMetadataRepo(ABC):
    @abstractmethod
    async def save(self, metadata: MessageFaceMetadata) -> None:
        """Persiste la metadata de caras de un mensaje (upsert por history_id).

        Args:
            metadata: Metadata a persistir. ``history_id`` es la PK.

        Raises:
            FaceRegistryError: Si falla la escritura en history.db.
        """
        ...

    @abstractmethod
    async def get_by_history_id(self, history_id: int) -> MessageFaceMetadata | None:
        """Recupera la metadata de caras para un mensaje específico.

        Args:
            history_id: ID del mensaje en el historial.

        Returns:
            Metadata encontrada o None si el mensaje no tiene caras asociadas.
        """
        ...

    @abstractmethod
    async def find_recent_for_thread(
        self,
        agent_id: str,
        channel: str,
        chat_id: str,
        limit: int = 10,
    ) -> list[MessageFaceMetadata]:
        """Recupera las N metadata más recientes para un hilo de conversación.

        Ordena por ``created_at DESC`` — las más recientes primero.

        Args:
            agent_id: ID del agente.
            channel: Canal de la conversación (ej: 'telegram').
            chat_id: ID del chat en el canal.
            limit: Máximo de resultados a retornar.

        Returns:
            Lista de MessageFaceMetadata, la más reciente primero.
        """
        ...

    @abstractmethod
    async def resolve_face_ref(
        self,
        agent_id: str,
        channel: str,
        chat_id: str,
        face_ref: str,
    ) -> tuple[MessageFaceMetadata, int] | None:
        """Resuelve un face_ref al par (metadata, face_idx) correspondiente.

        El face_ref tiene formato '{history_id}#{face_idx}'. Este método busca
        el mensaje de historial correspondiente y extrae el índice de cara.

        Args:
            agent_id: ID del agente (para scope de seguridad).
            channel: Canal del mensaje.
            chat_id: Chat del mensaje.
            face_ref: Referencia en formato '{history_id}#{face_idx}'.

        Returns:
            Tupla (MessageFaceMetadata, face_idx) si se encuentra, None si no.

        Raises:
            FaceRegistryError: Si el formato del face_ref es inválido.
        """
        ...
