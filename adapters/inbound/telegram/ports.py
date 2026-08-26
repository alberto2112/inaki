"""Contratos de entrada del TelegramBot — Ports y Settings VOs.

El bot NO recibe ``AgentContainer`` ni ``AgentConfig`` (infrastructure): declara
acá exactamente lo que consume, todo tipado contra ``core/``. El mapeo desde el
mundo config/container vive en los builders de ``infrastructure/container.py``
(``build_telegram_bot_settings`` / ``build_telegram_bot_ports``) — único punto
donde ambos mundos se tocan, igual que los Settings VOs de los use cases.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.ports.inbound.scheduler_port import IManualTaskRunner
from core.ports.outbound.file_downloader_port import IFileDownloader
from core.ports.outbound.scope_registry_port import IScopeRegistry
from core.ports.outbound.telegram_file_repo_port import IFileRecordRepo
from core.ports.outbound.transcription_port import ITranscriptionProvider
from core.use_cases.consolidate_memory import ConsolidateMemoryUseCase
from core.use_cases.process_photo import ProcessPhotoUseCase
from core.use_cases.reconcile_memory import ReconcileMemoryUseCase
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
    reconcile_memory: ReconcileMemoryUseCase | None = None
    schedule_task: ScheduleTaskUseCase | None = None
    manual_task_runner: IManualTaskRunner | None = None
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
class TelegramGroupSettings:
    """Política de respuesta y timing del bot en chats grupales.

    ``min_delay``/``max_delay`` en ``None`` significan "usá el default del
    módulo" (``group_flow``): la constante vive en el adapter, así que el VO
    la deja sin resolver en vez de duplicarla.
    """

    behavior: str = "mention"
    bot_username: str | None = None
    rate_limiter: int = 5
    rate_limiter_window: int = 30
    min_delay: float | None = None
    max_delay: float | None = None
    reactions: bool = False
    """Valor ya resuelto: el override de grupos si se declaró, si no el del canal."""


@dataclass(frozen=True)
class TelegramEmitFlags:
    """Qué eventos emite este agente al canal de broadcast del LAN."""

    assistant_response: bool = True
    user_input_voice: bool = False
    user_input_photo: bool = False


@dataclass(frozen=True)
class TelegramChannelSettings:
    """Slice tipado de ``channels.telegram`` que consume el bot.

    Reemplaza al dict crudo que el bot parseaba a mano en su ``__init__``: ese
    parseo re-declaraba los defaults que ya vivían en el schema (tercera copia)
    y tenía que defenderse con ``hasattr(x, "model_dump")`` porque el mismo
    campo llegaba como modelo o como dict según el camino. Hoy el bloque se
    valida en ``AgentConfig`` y el mapeo vive en un único builder del
    composition root.
    """

    token: str = ""
    allowed_user_ids: tuple[str, ...] = ()
    allowed_chat_ids: tuple[str, ...] = ()
    reactions: bool = False
    voice_enabled: bool = True
    groups: TelegramGroupSettings = field(default_factory=TelegramGroupSettings)
    emit: TelegramEmitFlags = field(default_factory=TelegramEmitFlags)


@dataclass(frozen=True)
class TelegramBotSettings:
    """Identidad del agente + slice de config que el bot consume.

    ``transcription=None`` significa que el agente no tiene transcripción
    configurada.
    """

    id: str
    name: str = ""
    description: str = ""
    workspace_path: str = ""
    transcription: TranscriptionLimits | None = None
    telegram: TelegramChannelSettings = field(default_factory=TelegramChannelSettings)
