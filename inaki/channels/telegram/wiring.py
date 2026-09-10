"""Wiring del canal Telegram: config → settings del bot, broadcast, tools, canal.

Un canal conoce ``inaki.config`` por contrato (registra su sección), así que
este fichero no necesita excepción: es la factory del canal. El composition
root lo llama con lo que el bot consume de otros módulos (``FuentesDelBot``) y
recibe piezas armadas; nunca sabe cómo se decide el rol del broadcast ni qué
tools registra un agente con token.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from inaki.channels.telegram.bot import TelegramBot
from inaki.channels.telegram.broadcast.buffer import BroadcastBuffer
from inaki.channels.telegram.broadcast.egress import BroadcastEgress
from inaki.channels.telegram.broadcast.rate_limiter import FixedWindowRateLimiter
from inaki.channels.telegram.broadcast.tcp import TcpBroadcastAdapter
from inaki.channels.telegram.channel import TelegramChannel
from inaki.channels.telegram.config import TelegramChannelConfig, telegram_config
from inaki.channels.telegram.files.downloader import TelegramFileDownloader
from inaki.channels.telegram.files.ports import IFileDownloader, IFileRecordRepo
from inaki.channels.telegram.files.repo import SqliteTelegramFileRepo
from inaki.channels.telegram.outbound import TelegramChannelOutbound
from inaki.channels.telegram.ports import (
    TelegramBotPorts,
    TelegramBotSettings,
    TelegramChannelSettings,
    TelegramEmitFlags,
    TelegramGroupSettings,
)
from inaki.channels.telegram.tools.download_from_telegram_tool import DownloadFromTelegramTool
from inaki.channels.telegram.tools.send_telegram_message_tool import SendTelegramMessageTool
from inaki.channels.telegram.tools.send_to_telegram_tool import SendToTelegramTool
from inaki.config import AgentConfig
from inaki.kernel.domain.channel_outbound_registry import ChannelOutboundRegistry
from inaki.kernel.ports.history_port import IHistoryStore
from inaki.kernel.ports.scope_registry_port import IScopeRegistry
from inaki.kernel.ports.tool_port import ITool
from inaki.kernel.conversation_history import ConversationHistory
from inaki.kernel.run_agent import RunAgentUseCase
from inaki.memory.use_cases.consolidate_memory import ConsolidateMemoryUseCase
from inaki.memory.use_cases.reconcile_memory import ReconcileMemoryUseCase
from inaki.observability import startup_event
from inaki.perception.use_cases.process_photo import ProcessPhotoUseCase
from inaki.perception.use_cases.transcribe_audio import TranscribeAudioUseCase
from inaki.scheduler.ports.use_case import IManualTaskRunner
from inaki.scheduler.use_cases.schedule_task import ScheduleTaskUseCase
from inaki.shared.channel_context import ChannelContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config → Settings VOs del bot
# ---------------------------------------------------------------------------


def build_telegram_channel_settings(
    tg_cfg: TelegramChannelConfig | None,
) -> TelegramChannelSettings:
    """Mapea el bloque ``channels.telegram`` ya validado → VO del adapter.

    Único punto donde se traduce config → slice del bot. Resuelve acá dos
    herencias que antes se rehacían dentro del bot: el ``reactions`` de grupos
    (override si existe, si no el del canal) y los flags de ``broadcast.emit``.
    """
    if tg_cfg is None:
        return TelegramChannelSettings()
    grupos = tg_cfg.groups
    group_settings = (
        TelegramGroupSettings(
            behavior=grupos.behavior,
            bot_username=grupos.bot_username,
            rate_limiter=grupos.rate_limiter,
            rate_limiter_window=grupos.rate_limiter_window,
            min_delay=grupos.min_delay_response,
            max_delay=grupos.max_delay_response,
            reactions=(tg_cfg.reactions if grupos.reactions is None else bool(grupos.reactions)),
        )
        if grupos is not None
        else TelegramGroupSettings(reactions=tg_cfg.reactions)
    )
    emit_cfg = tg_cfg.broadcast.emit if tg_cfg.broadcast is not None else None
    emit_flags = (
        TelegramEmitFlags(
            assistant_response=emit_cfg.assistant_response,
            user_input_voice=emit_cfg.user_input_voice,
            user_input_photo=emit_cfg.user_input_photo,
        )
        if emit_cfg is not None
        else TelegramEmitFlags()
    )
    return TelegramChannelSettings(
        token=tg_cfg.token,
        allowed_user_ids=tuple(str(uid) for uid in tg_cfg.allowed_user_ids),
        allowed_chat_ids=tuple(str(cid) for cid in tg_cfg.allowed_chat_ids),
        reactions=tg_cfg.reactions,
        voice_enabled=tg_cfg.voice_enabled,
        groups=group_settings,
        emit=emit_flags,
    )


def build_telegram_bot_settings(cfg: AgentConfig) -> TelegramBotSettings:
    """Mapea AgentConfig → slice de config que consume el TelegramBot."""
    return TelegramBotSettings(
        id=cfg.id,
        name=cfg.name,
        description=cfg.description,
        workspace_path=cfg.workspace.path,
        telegram=build_telegram_channel_settings(telegram_config(cfg)),
    )


class FuentesDelBot(Protocol):
    """Lo que el bot consume de un agente ya wireado (scheduler, fotos, tools).

    Estructural: lo satisface el ``AgentRuntime`` (y el borrador del ensamblador).
    """

    run_agent: RunAgentUseCase
    history: ConversationHistory
    scope_registry: IScopeRegistry
    consolidate_memory: ConsolidateMemoryUseCase | None
    reconcile_memory: ReconcileMemoryUseCase | None
    schedule_task: ScheduleTaskUseCase | None
    manual_task_runner: IManualTaskRunner | None
    process_photo: ProcessPhotoUseCase | None
    transcribe_audio: TranscribeAudioUseCase | None
    telegram_file_repo: IFileRecordRepo | None
    telegram_file_downloader: IFileDownloader | None
    channel_outbound_registry: ChannelOutboundRegistry


def build_telegram_bot_ports(fuente: FuentesDelBot) -> TelegramBotPorts:
    """Snapshot de las dependencias del bot. Llamar DESPUÉS de las fases de
    wiring (scheduler, photos, telegram tools): los campos opcionales capturan
    el valor wired, no una referencia al agente."""
    registro = fuente.channel_outbound_registry
    return TelegramBotPorts(
        run_agent=fuente.run_agent,
        history=fuente.history,
        scope_registry=fuente.scope_registry,
        consolidate_memory=fuente.consolidate_memory,
        reconcile_memory=fuente.reconcile_memory,
        schedule_task=fuente.schedule_task,
        manual_task_runner=fuente.manual_task_runner,
        process_photo=fuente.process_photo,
        transcribe_audio=fuente.transcribe_audio,
        telegram_file_repo=fuente.telegram_file_repo,
        telegram_file_downloader=fuente.telegram_file_downloader,
        channel_outbound=(
            registro.get("telegram") if "telegram" in registro.list_channels() else None
        ),
    )


# ---------------------------------------------------------------------------
# Broadcast y grupos
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TelegramAgentResources:
    """Lo derivado de ``channels.telegram`` de un agente, antes de que exista el bot."""

    egress: BroadcastEgress
    """Política de emisión al LAN (flags ``emit.*``): la comparten outbound y bot."""
    broadcast: TcpBroadcastAdapter | None
    """Transporte TCP; ``None`` sin bloque ``broadcast:`` o con ``enabled=false``."""
    rate_limiter: FixedWindowRateLimiter | None
    """Rate limiter de grupos (``behavior=autonomous``). No depende del broadcast."""


def build_broadcast(agent_cfg: AgentConfig) -> TelegramAgentResources | None:
    """``None`` si el agente no tiene canal telegram. Cada recurso deja un
    ``startup_event`` (nota ``broadcast-arranque-observable``): lo que la config
    declara y no se levanta tiene que verse en el log.

    Rol del broadcast por bloque nombrado: ``server`` (escucha en "0.0.0.0") XOR
    ``client`` (conecta a ``client.host:port``); la topología ya viene validada.
    """
    tg_cfg = telegram_config(agent_cfg)
    if tg_cfg is None:
        return None
    flags = build_telegram_channel_settings(tg_cfg).emit
    rate_limiter: FixedWindowRateLimiter | None = None
    groups_cfg = tg_cfg.groups
    if groups_cfg is not None and groups_cfg.behavior == "autonomous":
        rate_limiter = FixedWindowRateLimiter(window_seconds=float(groups_cfg.rate_limiter_window))
        startup_event(
            logger,
            "group_rate_limiter",
            status="ok",
            agent=agent_cfg.id,
            window_seconds=groups_cfg.rate_limiter_window,
        )
    broadcast_cfg = tg_cfg.broadcast
    if broadcast_cfg is None or not broadcast_cfg.enabled:
        startup_event(
            logger,
            "broadcast",
            status="skip",
            agent=agent_cfg.id,
            reason="sin bloque broadcast" if broadcast_cfg is None else "enabled=false",
        )
        return TelegramAgentResources(
            egress=BroadcastEgress(None, agent_cfg.id, flags),
            broadcast=None,
            rate_limiter=rate_limiter,
        )
    role: Literal["server", "client"]
    if broadcast_cfg.server is not None:
        role, host, port = "server", "0.0.0.0", broadcast_cfg.server.port
    else:
        assert broadcast_cfg.client is not None  # satisface narrowing de mypy
        role, host, port = "client", broadcast_cfg.client.host, broadcast_cfg.client.port
    assert broadcast_cfg.auth is not None  # validado con enabled=True
    adapter = TcpBroadcastAdapter(
        agent_id=agent_cfg.id,
        role=role,
        host=host,
        port=port,
        auth=broadcast_cfg.auth,
        buffer=BroadcastBuffer(),
    )
    startup_event(
        logger, "broadcast", status="ok", agent=agent_cfg.id, role=role, host=host, port=port
    )
    return TelegramAgentResources(
        egress=BroadcastEgress(adapter, agent_cfg.id, flags),
        broadcast=adapter,
        rate_limiter=rate_limiter,
    )


# ---------------------------------------------------------------------------
# Ficheros, outbound y tools
# ---------------------------------------------------------------------------


def build_telegram_file_repo(db_path: str) -> SqliteTelegramFileRepo:
    """Singleton del harness: cada record lleva ``agent_id`` para aislar por agente."""
    repo = SqliteTelegramFileRepo(db_path)
    logger.info("telegram_files.db inicializado: %s", db_path)
    return repo


def build_telegram_outbound(
    *,
    get_telegram_bot: Callable[[], object | None],
    history: IHistoryStore,
    agent_id: str,
    egress: BroadcastEgress | None,
    thinking_indicator: bool = False,
) -> TelegramChannelOutbound:
    """El egress único del canal: por acá salen TODOS los envíos del agente."""
    return TelegramChannelOutbound(
        get_telegram_bot=get_telegram_bot,
        history=history,
        agent_id=agent_id,
        broadcast=egress,
        shows_thinking=thinking_indicator,
    )


@dataclass(frozen=True)
class TelegramTools:
    downloader: TelegramFileDownloader
    tools: list[ITool]


def build_telegram_tools(
    cfg: AgentConfig,
    *,
    outbounds: ChannelOutboundRegistry,
    get_telegram_bot: Callable[[], object | None],
    file_repo: IFileRecordRepo,
    get_channel_context: Callable[[], ChannelContext | None],
) -> TelegramTools:
    """``send_to_telegram``, ``send_telegram_message`` y ``download_from_telegram``."""
    workspace = Path(cfg.workspace.path).expanduser().resolve()
    downloader = TelegramFileDownloader(get_telegram_bot=get_telegram_bot)
    tools: list[ITool] = [
        SendToTelegramTool(
            registry=outbounds,
            workspace=workspace,
            containment=cfg.workspace.containment,
            get_channel_context=get_channel_context,
            tool_calls_persisted=cfg.chat_history.persist_tool_calls,
        ),
        SendTelegramMessageTool(registry=outbounds),
        DownloadFromTelegramTool(
            repo=file_repo,
            downloader=downloader,
            workspace=workspace,
            agent_id=cfg.id,
            get_channel_context=get_channel_context,
        ),
    ]
    return TelegramTools(downloader=downloader, tools=tools)


# ---------------------------------------------------------------------------
# El canal
# ---------------------------------------------------------------------------


def build_channel(
    cfg: AgentConfig,
    ports: TelegramBotPorts,
    resources: TelegramAgentResources | None,
    *,
    reloader: object | None,
) -> tuple[TelegramBot, TelegramChannel]:
    """Bot + canal (``IChannel``) de un agente con token. Lanza ``ValueError`` si
    el bot no se puede construir; el composition root decide si degradar."""
    bot = TelegramBot(
        build_telegram_bot_settings(cfg),
        ports,
        broadcast_emitter=resources.broadcast if resources else None,
        broadcast_receiver=resources.broadcast if resources else None,
        rate_limiter=resources.rate_limiter if resources else None,
        reloader=reloader,
    )
    return bot, TelegramChannel(cfg.id, bot, resources.broadcast if resources else None)
