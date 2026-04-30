"""Port para el proveedor de visión / detección facial.

El adaptador concreto (InsightFace) implementa este port.
La implementación lazy-loadea el modelo en la primera llamada — NO en ``__init__``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.domain.entities.face import FaceDetection


class IVisionPort(ABC):
    @abstractmethod
    async def detect_and_embed(self, image_bytes: bytes) -> list[FaceDetection]:
        """Detecta caras en la imagen y devuelve sus embeddings.

        Combina detección y embedding en una sola llamada (InsightFace lo hace
        en un único pase). No separar en dos métodos.

        Args:
            image_bytes: Bytes de la imagen (JPEG o PNG).

        Returns:
            Lista de ``FaceDetection`` ordenados por ``detection_score`` desc.
            Lista vacía si no se detectan caras.

        Raises:
            VisionError: Si la imagen es inválida o el modelo falla.
        """
        ...
