"""Kind de payload genérico para envío saliente por cualquier canal.

Define los tipos de contenido que un canal puede enviar. Independiente de la
tecnología: un canal puede soportar solo TEXT, otro puede soportar todos. Los
adapters declaran sus capacidades vía ``IChannelOutbound.capabilities()``.

``ALBUM`` representa un grupo de imágenes enviadas juntas (multi-foto). Si el
canal no soporta álbumes nativamente, el adapter puede delegar a múltiples
envíos individuales de ``PHOTO``.
"""

from __future__ import annotations

from enum import Enum


class OutboundKind(str, Enum):
    """Tipos de payload saliente soportados por el sistema de canales."""

    TEXT = "text"
    PHOTO = "photo"
    AUDIO = "audio"
    VIDEO = "video"
    FILE = "file"
    ALBUM = "album"
