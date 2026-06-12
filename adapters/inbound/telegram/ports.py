"""Contratos de entrada del TelegramBot — Ports y Settings VOs.

El bot NO recibe ``AgentContainer`` ni ``AgentConfig`` (infrastructure): declara
acá exactamente lo que consume, todo tipado contra ``core/``. El mapeo desde el
mundo config/container vive en los builders de ``infrastructure/container.py``
(``build_telegram_bot_settings`` / ``build_telegram_bot_ports``) — único punto
donde ambos mundos se tocan, igual que los Settings VOs de los use cases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.ports.outbound.file_downloader_port import IFileDownloader
from core.ports.outbound.scope_registry_port import IScopeRegistry
from core.ports.outbound.telegram_file_repo_port import IFileRecordRepo
from core.ports.outbound.transcription_port import ITranscriptionProvider
from core.use_cases.consolidate_memory import ConsolidateMemoryUseCase
from core.use_cases.process_photo import ProcessPhotoUseCase
from core.use_cases.run_agent import RunAgentUseCase
from core.use_cases.schedule_task import ScheduleTaskUseCase


@dataclass(frozen=True)
class TelegramBotPorts:
    """Dependencias que el bot consume — snapshot al construir el bot.

    Los campos opcionales reflejan features no wireadas para el agente
    (fotos, scheduler, transcripción, repo de files): el bot degrada con
    aviso o silencio según el caso, igual que hacía con los ``getattr``
    defensivos sobre el container.
    """

    run_agent: RunAgentUseCase
    scope_registry: IScopeRegistry
    consolidate_memory: ConsolidateMemoryUseCase | None = None
    schedule_task: ScheduleTaskUseCase | None = None
    process_photo: ProcessPhotoUseCase | None = None
    transcription: ITranscriptionProvider | None = None
    telegram_file_repo: IFileRecordRepo | None = None
    telegram_file_downloader: IFileDownloader | None = None


@dataclass(frozen=True)
class TranscriptionLimits:
    """Slice de la config de transcripción que el bot necesita para el size-check."""

    language: str | None = None
    max_audio_mb: int = 25


@dataclass(frozen=True)
class TelegramBotSettings:
    """Identidad del agente + slice de config que el bot consume.

    ``telegram`` es el dict crudo de ``channels.telegram`` — el parseo de sus
    claves (token, allowed ids, broadcast, groups) es lógica del adapter y
    vive en ``TelegramBot.__init__``. ``transcription=None`` significa que el
    agente no tiene transcripción configurada.
    """

    id: str
    name: str = ""
    description: str = ""
    workspace_path: str = ""
    transcription: TranscriptionLimits | None = None
    telegram: dict[str, Any] = field(default_factory=dict)
