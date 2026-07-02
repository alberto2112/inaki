"""Gramática canónica de attachments entrantes — fuente ÚNICA del formato.

Todo media que llega por un canal (foto, audio, video, documento, álbum) deja
SIEMPRE un bloque de texto en ``history.db`` con esta gramática. El canal
(Telegram hoy, cualquier otro mañana) solo recolecta los datos crudos y llama
a los formatters de este módulo — NUNCA inventa su propio dialecto (regla del
canal THIN). El LLM aprende a leer estos bloques vía ``ATTACHMENTS_SECTION``
del system prompt (``core/use_cases/_turn_pipeline.py``).

Formato (texto fijo en INGLÉS — convención system-prompts-language):

    @photo at /abs/path.jpg
    @audio voz.ogg (audio/ogg) at /abs/path.ogg
    @transcription: hola, necesito que...
    @file informe.pdf (application/pdf) at /abs/path.pdf
    @caption: resumime esto
    @album (3 items):
    @photo at /abs/1.jpg
    @photo at /abs/2.jpg

Modo degradado (la pre-descarga falló): la línea principal termina en
``pending (id: <file_ref>) — retrieve with download_from_telegram`` para que
el LLM sepa recuperarlo por su cuenta.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

#: Tipos atómicos de attachment. ``album`` NO es un tipo: es una agrupación
#: (ver ``format_album``), igual que en ``telegram_file.py``.
AttachmentType = Literal["photo", "audio", "video", "file"]


class IncomingAttachment(BaseModel, frozen=True):
    """Un media entrante ya resuelto por el canal, listo para formatear.

    ``path`` es el path local ABSOLUTO tras la pre-descarga al workspace;
    ``None`` significa que la descarga falló y el bloque sale en modo
    degradado usando ``file_ref`` (id estable del canal, ej: el
    ``file_unique_id`` de Telegram — el mismo basename que tendría el path).
    """

    type: AttachmentType
    path: str | None = None
    name: str | None = None
    mime: str | None = None
    file_ref: str | None = None

    def header_line(self) -> str:
        """Línea principal del bloque: ``@<type> [name] [(mime)] at <path>``."""
        parts = [f"@{self.type}"]
        if self.name:
            parts.append(self.name)
        if self.mime:
            parts.append(f"({self.mime})")
        if self.path:
            parts.append(f"at {self.path}")
        else:
            ref = f" (id: {self.file_ref})" if self.file_ref else ""
            parts.append(f"pending{ref} — retrieve with download_from_telegram")
        return " ".join(parts)


def _aux_lines(
    *,
    transcription: str | None = None,
    analysis: str | None = None,
    caption: str | None = None,
) -> list[str]:
    """Líneas auxiliares en orden fijo: transcription → analysis → caption.

    El caption va último a propósito: es la voz del usuario y queda pegado a
    donde el LLM espera la instrucción del turno.
    """
    lines: list[str] = []
    if transcription and transcription.strip():
        lines.append(f"@transcription: {transcription.strip()}")
    if analysis and analysis.strip():
        lines.append(f"@analysis: {analysis.strip()}")
    if caption and caption.strip():
        lines.append(f"@caption: {caption.strip()}")
    return lines


def format_attachment(
    attachment: IncomingAttachment,
    *,
    transcription: str | None = None,
    analysis: str | None = None,
    caption: str | None = None,
) -> str:
    """Formatea UN attachment con sus líneas auxiliares opcionales."""
    return "\n".join(
        [
            attachment.header_line(),
            *_aux_lines(transcription=transcription, analysis=analysis, caption=caption),
        ]
    )


def format_album(
    members: list[IncomingAttachment],
    *,
    caption: str | None = None,
) -> str:
    """Formatea un álbum: encabezado con conteo + una línea por miembro.

    Sin miembros (el gather no recuperó nada), degrada a un bloque ``pending``
    para que el LLM sepa que llegó un álbum y cómo recuperarlo.
    """
    if not members:
        header = "@album pending — retrieve with download_from_telegram"
        return "\n".join([header, *_aux_lines(caption=caption)])
    lines = [f"@album ({len(members)} items):"]
    lines.extend(m.header_line() for m in members)
    lines.extend(_aux_lines(caption=caption))
    return "\n".join(lines)
