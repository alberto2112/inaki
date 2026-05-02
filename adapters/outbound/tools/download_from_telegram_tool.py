"""DownloadFromTelegramTool — descarga archivos recibidos por Telegram.

Lee del repo dedicado (``telegram_files.db``) los registros más recientes del
chat actual y descarga vía ``IFileDownloader`` a ``<workspace>/telegram/``.

Cache: si el archivo ya existe en disco con el ``file_unique_id`` esperado,
no se re-descarga. Telegram garantiza que ``file_unique_id`` es estable.

Filtros temporales (``since`` / ``until``):
- Sin ``since`` → últimas ``count`` sin filtro (``until`` se ignora si viene solo).
- ``since`` solo → ``until`` default = ahora (UTC).
- ``since`` + ``until`` → rango cerrado.
- Formato ISO 8601. Si NO trae offset, se asume UTC.
"""

from __future__ import annotations

import json
import logging
import mimetypes
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.domain.value_objects.channel_context import ChannelContext
from core.domain.value_objects.telegram_file import (
    DownloadableContentType,
    TelegramFileRecord,
)
from core.ports.outbound.file_downloader_port import IFileDownloader
from core.ports.outbound.telegram_file_repo_port import IFileRecordRepo
from core.ports.outbound.tool_port import ITool, ToolResult

logger = logging.getLogger(__name__)


_TIPOS_VALIDOS: tuple[DownloadableContentType, ...] = (
    "photo",
    "album",
    "audio",
    "video",
    "file",
)

# Fallbacks por content_type cuando no podemos inferir extensión del MIME.
_DEFAULT_EXT: dict[str, str] = {
    "photo": ".jpg",
    "audio": ".ogg",
    "video": ".mp4",
    "file": ".bin",
}


class DownloadFromTelegramTool(ITool):
    name = "download_from_telegram"
    description = (
        "Download recent media (photos, albums, audios, videos, files) sent by "
        "the user in the CURRENT Telegram chat to the agent's workspace, then "
        "return the local paths. "
        "Required: 'content_type' (one of 'photo', 'album', 'audio', 'video', "
        "'file'). "
        "Optional: 'count' (default 5), 'since' / 'until' (ISO 8601 datetimes; "
        "without offset they are interpreted as UTC). "
        "Without 'since' the tool returns the latest 'count' files unfiltered. "
        "Files are cached by Telegram's stable 'file_unique_id': repeated "
        "downloads of the same file are no-ops. "
        "WHEN TO USE: when the user message arrives prefixed with one of "
        "'__FILE__ <name>', '__VIDEO__ <name>', or '__ALBUM__', it means the "
        "user attached that media to their message — call this tool with the "
        "matching content_type and count=1 (or more for albums) to get the "
        "local path before doing anything else with the file."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "content_type": {
                "type": "string",
                "enum": list(_TIPOS_VALIDOS),
            },
            "count": {
                "type": "integer",
                "description": "Maximum number of files to return (default 5).",
                "default": 5,
                "minimum": 1,
            },
            "since": {
                "type": "string",
                "description": "ISO 8601 datetime; without offset assumed UTC.",
            },
            "until": {
                "type": "string",
                "description": (
                    "ISO 8601 datetime; without offset assumed UTC. Ignored if "
                    "'since' is not provided."
                ),
            },
        },
        "required": ["content_type"],
    }

    def __init__(
        self,
        repo: IFileRecordRepo,
        downloader: IFileDownloader,
        workspace: Path,
        agent_id: str,
        get_channel_context: Callable[[], ChannelContext | None],
    ) -> None:
        self._repo = repo
        self._downloader = downloader
        self._workspace = workspace
        self._agent_id = agent_id
        self._get_channel_context = get_channel_context

    async def execute(self, **kwargs: Any) -> ToolResult:
        content_type = str(kwargs.get("content_type") or "").strip().lower()
        if content_type not in _TIPOS_VALIDOS:
            return self._fail(
                f"content_type inválido: {content_type!r}. "
                f"Válidos: {sorted(_TIPOS_VALIDOS)}",
                retryable=False,
            )

        count_raw = kwargs.get("count", 5)
        try:
            count = int(count_raw)
        except (TypeError, ValueError):
            return self._fail(f"'count' debe ser entero, recibí {count_raw!r}", retryable=False)
        if count <= 0:
            return self._fail("'count' debe ser >= 1", retryable=False)

        try:
            since, until = self._parse_window(kwargs.get("since"), kwargs.get("until"))
        except ValueError as exc:
            return self._fail(str(exc), retryable=False)

        ctx = self._get_channel_context()
        if ctx is None:
            return self._fail(
                "download_from_telegram solo funciona dentro de una conversación activa.",
                retryable=False,
            )
        if ctx.channel_type != "telegram":
            return self._fail(
                f"download_from_telegram no soporta el canal '{ctx.channel_type}'.",
                retryable=False,
            )
        if not ctx.chat_id:
            return self._fail(
                "no hay chat_id en el contexto del turno — no puedo descargar.",
                retryable=False,
            )

        records = await self._repo.query_recent(
            agent_id=self._agent_id,
            channel="telegram",
            chat_id=ctx.chat_id,
            content_type=content_type,  # type: ignore[arg-type]
            count=count,
            since=since,
            until=until,
        )

        download_dir = self._workspace / "telegram"
        download_dir.mkdir(parents=True, exist_ok=True)

        files_payload: list[dict] = []
        for record in records:
            try:
                path = await self._descargar(record, download_dir)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "download_from_telegram: error descargando file_id=%s", record.file_id
                )
                return self._fail(
                    f"error descargando {record.file_unique_id}: {exc}",
                    retryable=True,
                )
            files_payload.append(
                {
                    "path": str(path),
                    "content_type": record.content_type,
                    "media_group_id": record.media_group_id,
                    "caption": record.caption,
                    "received_at": record.received_at.astimezone(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "mime_type": record.mime_type,
                }
            )

        payload = {"files": files_payload, "count": len(files_payload)}
        return ToolResult(
            tool_name=self.name,
            output=json.dumps(payload, ensure_ascii=False),
            success=True,
        )

    async def _descargar(self, record: TelegramFileRecord, download_dir: Path) -> Path:
        ext = _extension_para(record)
        dest = download_dir / f"{record.file_unique_id}{ext}"
        if dest.exists():
            logger.debug("download_from_telegram cache hit: %s", dest)
            return dest
        await self._downloader.download(file_id=record.file_id, dest=dest)
        return dest

    @staticmethod
    def _parse_window(
        since_raw: object, until_raw: object
    ) -> tuple[datetime | None, datetime | None]:
        since = _parse_iso_utc(since_raw, "since")
        until = _parse_iso_utc(until_raw, "until")

        if since is None:
            # Sin since → ignoramos until y devolvemos sin filtro (semántica del usuario).
            return None, None
        if until is None:
            until = datetime.now(timezone.utc)
        if until < since:
            raise ValueError("'until' debe ser >= 'since'")
        return since, until

    def _fail(self, message: str, *, retryable: bool) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            output=json.dumps({"success": False, "error": message}, ensure_ascii=False),
            success=False,
            error=message,
            retryable=retryable,
        )


def _parse_iso_utc(raw: object, label: str) -> datetime | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        # ``fromisoformat`` acepta tanto "2026-05-01T12:00:00" como con offset.
        dt = datetime.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{label} no es un ISO 8601 válido: {raw!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _extension_para(record: TelegramFileRecord) -> str:
    if record.mime_type:
        ext = mimetypes.guess_extension(record.mime_type)
        if ext:
            return ext
    return _DEFAULT_EXT.get(record.content_type, ".bin")
