"""Value objects para los ficheros recibidos vía Telegram.

El sistema persiste el ``file_id`` de cada media que llega para que el LLM
pueda recuperarlo después vía ``download_from_telegram``. Los bytes nunca se
guardan en el repo: Telegram conserva los archivos por su ``file_id`` y se
descargan on-demand al workspace del agente.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator

#: Tipos de contenido atómicos que persiste el repo. ``album`` NO está acá:
#: es una agrupación derivada de varios ``photo`` con el mismo ``media_group_id``.
FileContentType = Literal["photo", "audio", "video", "file"]

#: Tipos válidos para ``download_from_telegram``: incluye ``album`` como
#: sintáctico para querear photos agrupadas.
DownloadableContentType = Literal["photo", "album", "audio", "video", "file"]


class TelegramFileRecord(BaseModel, frozen=True):
    """Registro de un fichero recibido por Telegram.

    El ``received_at`` se almacena SIEMPRE en UTC; el caller es responsable
    de convertir a UTC antes de instanciar.
    """

    agent_id: str
    channel: str
    chat_id: str
    content_type: FileContentType
    file_id: str
    file_unique_id: str
    media_group_id: str | None = None
    caption: str | None = None
    history_id: int | None = None
    mime_type: str | None = None
    received_at: datetime

    @field_validator("agent_id", "channel", "chat_id", "file_id", "file_unique_id", mode="before")
    @classmethod
    def _no_vacio(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("no puede ser vacío")
        return v

    @field_validator("received_at", mode="after")
    @classmethod
    def _debe_ser_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError(
                "received_at debe ser timezone-aware en UTC; recibí naive datetime"
            )
        return v
