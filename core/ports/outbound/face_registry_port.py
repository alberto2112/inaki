"""Port para el registro de personas conocidas (faces.db).

El adaptador concreto (SqliteFaceRegistry) implementa este port.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from core.domain.entities.face import FaceMatch, Person


class IFaceRegistryPort(ABC):
    @abstractmethod
    async def register_person(
        self,
        nombre: str | None,
        apellido: str | None,
        fecha_nacimiento: str | None,
        relacion: str | None,
        embedding: np.ndarray,
        source_history_id: int,
        source_face_ref: str,
        categoria: str | None = None,
    ) -> Person:
        """Registra una nueva persona con su embedding inicial.

        Para registrar una persona ignorada (skip_face):
            ``nombre=None, categoria='ignorada'``

        Args:
            nombre: Nombre de la persona. None para personas ignoradas.
            apellido: Apellido opcional.
            fecha_nacimiento: Fecha ISO YYYY-MM-DD opcional.
            relacion: Relación libre ('hijo', 'amigo', 'colega', etc.).
            embedding: Vector de embedding numpy de dimensión 512.
            source_history_id: ID del mensaje de historial del que proviene.
            source_face_ref: face_ref en formato '{history_id}#{idx}'.
            categoria: None para persona normal. 'ignorada' para skip_face.

        Returns:
            Persona creada con su ID asignado.

        Raises:
            FaceRegistryError: Si falla la escritura en faces.db.
        """
        ...

    @abstractmethod
    async def add_embedding_to_person(
        self,
        person_id: str,
        embedding: np.ndarray,
        source_history_id: int,
        source_face_ref: str,
    ) -> None:
        """Agrega un embedding adicional a una persona existente.

        Args:
            person_id: ID de la persona existente.
            embedding: Nuevo vector de embedding numpy.
            source_history_id: ID del mensaje de historial del que proviene.
            source_face_ref: face_ref en formato '{history_id}#{idx}'.

        Raises:
            FaceRegistryError: Si la persona no existe o falla la escritura.
        """
        ...

    @abstractmethod
    async def update_person_metadata(
        self,
        person_id: str,
        **fields: object,
    ) -> Person:
        """Actualiza campos de metadata de una persona (nombre, relacion, notes, etc.).

        Solo actualiza los campos presentes en ``fields``. Campos no incluidos
        se mantienen sin cambio.

        Args:
            person_id: ID de la persona a actualizar.
            **fields: Campos a actualizar (nombre, apellido, relacion, notes, etc.).

        Returns:
            Persona actualizada.

        Raises:
            FaceRegistryError: Si la persona no existe o falla la escritura.
        """
        ...

    @abstractmethod
    async def forget_person(self, person_id: str) -> None:
        """Elimina una persona y todos sus embeddings del registro.

        El CASCADE de la FK en person_embeddings y person_embeddings_vec
        se encarga de borrar los embeddings asociados.

        Args:
            person_id: ID de la persona a eliminar.

        Raises:
            FaceRegistryError: Si la persona no existe.
        """
        ...

    @abstractmethod
    async def merge_persons(self, source_id: str, target_id: str) -> Person:
        """Fusiona todos los embeddings de ``source`` en ``target`` y elimina ``source``.

        Args:
            source_id: ID de la persona que se borrará (fuente de los embeddings).
            target_id: ID de la persona que los absorbe (destino).

        Returns:
            Persona ``target`` actualizada con el conteo combinado.

        Raises:
            FaceRegistryError: Si alguno de los IDs no existe.
        """
        ...

    @abstractmethod
    async def find_matches(
        self,
        embedding: np.ndarray,
        k: int = 3,
    ) -> list[FaceMatch]:
        """Busca las k personas más similares al embedding dado.

        Devuelve TODAS las coincidencias incluyendo personas ignoradas
        (``categoria='ignorada'``). El use case decide cómo filtrar.
        El campo ``FaceMatch.categoria`` expone la categoría del mejor candidato.

        Args:
            embedding: Vector de embedding numpy de dimensión 512.
            k: Número máximo de candidatos por cara.

        Returns:
            Lista de FaceMatch ordenados por score descendente. Vacía si no hay
            personas en el registro.

        Raises:
            FaceRegistryError: Si falla la consulta.
        """
        ...

    @abstractmethod
    async def list_persons(
        self,
        incluir_ignoradas: bool = False,
    ) -> list[Person]:
        """Lista todas las personas registradas.

        Por defecto excluye personas con ``categoria='ignorada'`` para no
        contaminar la vista del LLM con ruido.

        Args:
            incluir_ignoradas: Si True, incluye personas ignoradas en la lista.

        Returns:
            Lista de personas ordenadas por nombre.
        """
        ...

    @abstractmethod
    async def get_person(self, person_id: str) -> Person | None:
        """Recupera una persona por su ID.

        Args:
            person_id: ID de la persona.

        Returns:
            Persona encontrada o None si no existe.
        """
        ...

    @abstractmethod
    async def get_centroid(self, person_id: str) -> np.ndarray | None:
        """Calcula el centroide (promedio) de todos los embeddings de una persona.

        Usado por el job de deduplicación para comparar pares de personas.

        Args:
            person_id: ID de la persona.

        Returns:
            Vector centroide numpy o None si la persona no tiene embeddings.
        """
        ...
