"""Entidades de dominio para el reconocimiento facial.

Este módulo define las entidades puras del dominio (sin dependencias externas).
Solo stdlib + pydantic. Nunca importar desde adapters/ o infrastructure/.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Person(BaseModel):
    """Persona conocida por el sistema de reconocimiento facial.

    ``nombre=None`` y ``categoria='ignorada'`` identifica a una persona ignorada
    (registrada via ``skip_face``). Estas personas se excluyen silenciosamente
    del output del agente en futuras fotos.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nombre: str | None = None
    """None para personas ignoradas (skip_face). Obligatorio para personas conocidas."""
    apellido: str | None = None
    fecha_nacimiento: str | None = None
    """Fecha en formato ISO YYYY-MM-DD."""
    relacion: str | None = None
    """Relación libre: 'hijo', 'amigo', 'colega', etc."""
    notes: str | None = None
    categoria: str | None = None
    """None = persona normal conocida. 'ignorada' = marcada vía skip_face.
    Extensible: 'extra_de_fondo', 'desconocida_confirmada', etc."""
    embeddings_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BBox(BaseModel):
    """Bounding box de una cara detectada en la imagen (coordenadas en píxeles)."""

    x: int
    y: int
    w: int
    h: int


class FaceDetection(BaseModel):
    """Resultado de detectar una cara en una imagen.

    ``embedding`` es una lista de 512 floats (InsightFace buffalo_sc).
    Se serializa como lista (no numpy) para ser compatible con JSON y pydantic.
    """

    bbox: BBox
    embedding: list[float]
    """Vector de embedding de 512 floats. numpy.ndarray se convierte antes de crear esta entidad."""
    detection_score: float
    """Confianza del detector (0.0–1.0). InsightFace típicamente ≥ 0.5 para caras reales."""


class MatchStatus(str, Enum):
    """Estado del matching de una cara contra el registro de personas."""

    MATCHED = "matched"
    """Score ≥ match_threshold → persona identificada con confianza."""
    AMBIGUOUS = "ambiguous"
    """Score entre ambiguous_threshold y match_threshold → pedir confirmación al usuario."""
    UNKNOWN = "unknown"
    """Score < ambiguous_threshold → cara desconocida."""


class FaceMatch(BaseModel):
    """Resultado de intentar identificar una cara detectada en el registro de personas.

    ``face_ref`` tiene el formato ``"{history_id}#{face_idx}"`` (ej: ``"4231#0"``).
    Es único globalmente y resoluble en O(1) via ``IMessageFaceMetadataRepo.resolve_face_ref``.
    """

    face_ref: str
    """Referencia única a esta cara: '{history_id}#{face_idx}'."""
    bbox: BBox
    candidates: list[tuple[Person, float]]
    """Lista de candidatos (Persona, score_similitud) ordenados por score desc."""
    status: MatchStatus
    categoria: str | None = None
    """Categoría del mejor candidato. Propagado de Person.categoria.
    None si no hay candidatos o el mejor es una persona normal."""


class MessageFaceMetadata(BaseModel):
    """Metadata de las caras detectadas en un mensaje de foto.

    Se persiste como side-table en history.db keyed por history_id.
    ON DELETE CASCADE garantiza limpieza cuando se borra el historial.
    """

    history_id: int
    """FK lógica a history.id. No enforced en la entidad — el repo gestiona la FK."""
    agent_id: str
    channel: str
    chat_id: str
    faces: list[FaceMatch]
    """Lista de FaceMatch — una por cara detectada en la foto. Puede ser vacía."""
    embeddings_blob: bytes
    """numpy.savez_compressed de todas las detecciones. Permite resolver face_ref → embedding."""
    created_at: datetime


@dataclass(frozen=True)
class ProcessPhotoResult:
    """Resultado del caso de uso ProcessPhotoUseCase.

    ``text_context`` es el texto enriquecido con información de caras y escena
    que se inyecta como contexto adicional al agente de lenguaje.

    ``annotated_image`` es la imagen anotada con números sobre caras desconocidas,
    lista para ser enviada al usuario. None si no hay caras desconocidas o si el
    procesamiento fue omitido.

    ``should_skip_run_agent`` indica que la foto fue ignorada (ej: fotos deshabilitadas
    en config) y el agente de lenguaje NO debe ejecutarse para este mensaje.
    """

    text_context: str
    annotated_image: bytes | None = None
    should_skip_run_agent: bool = False
