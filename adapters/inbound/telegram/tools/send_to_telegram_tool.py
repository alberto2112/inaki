"""SendToTelegramTool — el LLM adjunta archivos al chat actual de Telegram.

Reemplaza la antigua ``send_photo``. Soporta photo / audio / video / file
individual y ``album`` (lista de fotos enviadas como media group).

El destino siempre es el chat actual del turno (resuelto del ``ChannelContext``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from adapters.outbound.tools.path_resolution import (
    ContainmentMode,
    WorkspaceEscapeError,
    resolve_path,
)
from core.domain.value_objects.channel_context import ChannelContext
from core.domain.value_objects.telegram_file import FileContentType
from core.ports.outbound.file_sender_port import IFileSender
from core.ports.outbound.tool_port import ITool, ToolResult

logger = logging.getLogger(__name__)


_TIPOS_INDIVIDUALES: tuple[FileContentType, ...] = ("photo", "audio", "video", "file")
_TIPOS_VALIDOS = (*_TIPOS_INDIVIDUALES, "album")


class SendToTelegramTool(ITool):
    name = "send_to_telegram"
    description = (
        "Send a file to the CURRENT Telegram chat (the one you're talking in). "
        "Required: 'content_type' (one of 'photo', 'audio', 'video', 'file', "
        "'album') and 'filename' (a local path under the agent's workspace). "
        "For 'album', 'filename' MUST be an array of paths to photos. "
        "Optional: 'caption' (shown under the file; for albums applied to the "
        "first photo). The destination is automatic — you cannot choose which "
        "chat receives the file."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "content_type": {
                "type": "string",
                "enum": list(_TIPOS_VALIDOS),
                "description": (
                    "'photo' | 'audio' | 'video' | 'file' | 'album'. "
                    "Use 'album' to send several photos grouped together."
                ),
            },
            "filename": {
                "description": (
                    "Local path under the workspace, OR (for 'album') an array "
                    "of paths."
                ),
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}, "minItems": 1},
                ],
            },
            "caption": {
                "type": "string",
                "description": "Optional caption shown under the file.",
            },
        },
        "required": ["content_type", "filename"],
    }

    def __init__(
        self,
        sender: IFileSender,
        workspace: Path,
        containment: ContainmentMode,
        get_channel_context: Callable[[], ChannelContext | None],
    ) -> None:
        self._sender = sender
        self._workspace = workspace
        self._containment = containment
        self._get_channel_context = get_channel_context

    async def execute(self, **kwargs: Any) -> ToolResult:
        content_type = str(kwargs.get("content_type") or "").strip().lower()
        if content_type not in _TIPOS_VALIDOS:
            return self._fail(
                f"content_type inválido: {content_type!r}. "
                f"Válidos: {sorted(_TIPOS_VALIDOS)}",
                retryable=False,
            )

        filename_raw = kwargs.get("filename")
        caption = kwargs.get("caption")
        if caption is not None:
            caption = str(caption).strip() or None

        ctx = self._get_channel_context()
        if ctx is None:
            return self._fail(
                "send_to_telegram solo funciona dentro de una conversación activa.",
                retryable=False,
            )
        if ctx.channel_type != "telegram":
            return self._fail(
                f"send_to_telegram no soporta el canal '{ctx.channel_type}' (solo telegram).",
                retryable=False,
            )
        if not ctx.chat_id:
            return self._fail(
                "no hay chat_id en el contexto del turno — no puedo enviar.",
                retryable=False,
            )

        try:
            paths = self._resolver_paths(filename_raw, content_type)
        except (ValueError, WorkspaceEscapeError, FileNotFoundError) as exc:
            return self._fail(str(exc), retryable=False)

        try:
            if content_type == "album":
                await self._sender.send_album(
                    chat_id=ctx.chat_id, sources=paths, caption=caption
                )
            else:
                await self._sender.send(
                    chat_id=ctx.chat_id,
                    content_type=content_type,  # type: ignore[arg-type]
                    source=paths[0],
                    caption=caption,
                )
        except (FileNotFoundError, ValueError) as exc:
            return self._fail(str(exc), retryable=False)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "send_to_telegram: error enviando %s a chat_id=%s",
                content_type,
                ctx.chat_id,
            )
            return self._fail(f"transport error: {exc}", retryable=True)

        payload = {
            "sent": True,
            "content_type": content_type,
            "count": len(paths),
            "chat_id": ctx.chat_id,
        }
        return ToolResult(
            tool_name=self.name,
            output=json.dumps(payload, ensure_ascii=False),
            success=True,
        )

    def _resolver_paths(
        self, filename_raw: object, content_type: str
    ) -> list[Path]:
        if content_type == "album":
            if not isinstance(filename_raw, list) or not filename_raw:
                raise ValueError(
                    "para content_type='album', 'filename' debe ser una lista no vacía de paths."
                )
            paths_str = [str(p).strip() for p in filename_raw]
            if any(not p for p in paths_str):
                raise ValueError("la lista de filenames contiene un path vacío.")
        else:
            if isinstance(filename_raw, list):
                raise ValueError(
                    f"para content_type={content_type!r}, 'filename' debe ser un único path (string), no una lista."
                )
            single = str(filename_raw or "").strip()
            if not single:
                raise ValueError("'filename' es requerido y no puede ser vacío.")
            paths_str = [single]

        resueltos: list[Path] = []
        for raw in paths_str:
            resolved = resolve_path(raw, self._workspace, self._containment)
            if not resolved.exists():
                raise FileNotFoundError(f"archivo no encontrado: {resolved}")
            resueltos.append(resolved)
        return resueltos

    def _fail(self, message: str, *, retryable: bool) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            output=json.dumps({"success": False, "error": message}, ensure_ascii=False),
            success=False,
            error=message,
            retryable=retryable,
        )
