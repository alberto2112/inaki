"""
FileHistoryStore — historial de conversación en fichero de texto.

Un fichero por agente: data/history/active/{agent_id}.txt
Formato: una línea por mensaje, prefijo 'user: ' o 'assistant: '

Solo se persisten mensajes user y assistant — nunca tool calls.

Cache en memoria:
  Si history.max_messages_in_prompt > 0, load() devuelve desde una ventana
  en memoria (los últimos N*2 mensajes) sin leer el fichero en cada turno.
  load_full() siempre lee desde disco — usar solo para consolidación.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import aiofiles

from core.domain.entities.message import Message, Role
from core.domain.errors import HistoryError
from core.ports.outbound.history_port import IHistoryStore
from infrastructure.config import HistoryConfig

logger = logging.getLogger(__name__)


def _parse_line(line: str) -> Message | None:
    if line.startswith("user: "):
        return Message(role=Role.USER, content=line[6:])
    if line.startswith("assistant: "):
        return Message(role=Role.ASSISTANT, content=line[11:])
    return None


class FileHistoryStore(IHistoryStore):

    def __init__(self, cfg: HistoryConfig) -> None:
        self._active_dir = Path(cfg.active_dir)
        self._archive_dir = Path(cfg.archive_dir)
        self._active_dir.mkdir(parents=True, exist_ok=True)
        self._archive_dir.mkdir(parents=True, exist_ok=True)

        # max_n > 0 → ventana en memoria de los últimos max_n*2 mensajes
        # max_n = 0 → sin límite, load() lee desde disco siempre
        self._max_n = cfg.max_messages_in_prompt
        self._maxlen = self._max_n * 2 if self._max_n > 0 else None
        self._cache: dict[str, deque[Message]] = {}

    def _active_path(self, agent_id: str) -> Path:
        return self._active_dir / f"{agent_id}.txt"

    async def _warm_cache(self, agent_id: str) -> None:
        """Carga desde disco e inicializa el cache para el agente."""
        messages = await self._read_from_disk(agent_id)
        cache: deque[Message] = deque(maxlen=self._maxlen)
        cache.extend(messages)
        self._cache[agent_id] = cache

    async def _read_from_disk(self, agent_id: str) -> list[Message]:
        path = self._active_path(agent_id)
        if not path.exists():
            return []
        messages: list[Message] = []
        try:
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                async for line in f:
                    msg = _parse_line(line.rstrip("\n"))
                    if msg:
                        messages.append(msg)
        except OSError as exc:
            raise HistoryError(f"Error leyendo historial para '{agent_id}': {exc}") from exc
        return messages

    async def load(self, agent_id: str) -> list[Message]:
        """
        Retorna los mensajes del historial.
        Si max_messages_in_prompt > 0: devuelve desde cache en memoria (sin IO).
        Si max_messages_in_prompt = 0: lee el fichero completo desde disco.
        """
        if self._maxlen is not None:
            if agent_id not in self._cache:
                await self._warm_cache(agent_id)
            return list(self._cache[agent_id])
        return await self._read_from_disk(agent_id)

    async def load_full(self, agent_id: str) -> list[Message]:
        """Retorna el historial completo desde disco. Usar solo para consolidación."""
        return await self._read_from_disk(agent_id)

    async def append(self, agent_id: str, message: Message) -> None:
        if message.role not in (Role.USER, Role.ASSISTANT):
            return

        path = self._active_path(agent_id)
        line = f"{message.role.value}: {message.content}\n"
        try:
            async with aiofiles.open(path, "a", encoding="utf-8") as f:
                await f.write(line)
        except OSError as exc:
            raise HistoryError(f"Error escribiendo historial para '{agent_id}': {exc}") from exc

        if self._maxlen is not None:
            if agent_id not in self._cache:
                await self._warm_cache(agent_id)
            else:
                self._cache[agent_id].append(message)

    async def archive(self, agent_id: str) -> str:
        path = self._active_path(agent_id)
        if not path.exists():
            raise HistoryError(f"No hay historial activo para '{agent_id}'")

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive_path = self._archive_dir / f"{agent_id}_{timestamp}.txt"

        try:
            path.rename(archive_path)
        except OSError as exc:
            raise HistoryError(f"Error archivando historial para '{agent_id}': {exc}") from exc

        self._cache.pop(agent_id, None)
        logger.info("Historial de '%s' archivado en %s", agent_id, archive_path)
        return str(archive_path)

    async def clear(self, agent_id: str) -> None:
        path = self._active_path(agent_id)
        if path.exists():
            try:
                path.unlink()
            except OSError as exc:
                raise HistoryError(f"Error limpiando historial para '{agent_id}': {exc}") from exc
        self._cache.pop(agent_id, None)
