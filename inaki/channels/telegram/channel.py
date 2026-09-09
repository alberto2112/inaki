"""``TelegramChannel`` — el ciclo de vida del canal, implementando ``IChannel``.

Arranca y detiene JUNTOS el bot (polling de PTB) y, si lo hay, el transporte de
broadcast del agente. El daemon ya no sabe qué es Telegram: itera ``IChannel``.

Orden de arranque (cada paso tiene un porqué):

1. Broadcast: el ``bind()`` ocurre en ``start()`` y PROPAGA si falla — acá se
   loguea como ``startup.resource`` con ``status=error`` y el bot arranca igual
   (``broadcast-arranque-observable``): un bot sin LAN es útil; un bot que no
   arranca por el LAN, no.
2. ``Application.initialize()`` + ``start()``: el lifecycle es manual a propósito,
   NO ``run_polling`` — el daemon coordina varios canales en un solo loop.
3. Comandos, validación del ``bot_username``, suscripción al trigger de broadcast.
4. Aviso "online" a los chats que escribieron mientras el daemon estaba caído,
   ANTES del polling (``initialize()`` no dispara ``post_init``; ver
   ``_announce_back_online``), y por eso el polling arranca con
   ``drop_pending_updates=False``: el backlog ya se confirmó a mano.
"""

from __future__ import annotations

import logging

from inaki.kernel.ports.outbound.channel_port import IChannel
from inaki.channels.telegram.bot import TelegramBot
from inaki.channels.telegram.broadcast.tcp import TcpBroadcastAdapter
from inaki.observability import startup_event

logger = logging.getLogger(__name__)


class TelegramChannel(IChannel):
    name = "telegram"

    def __init__(
        self,
        agent_id: str,
        bot: TelegramBot,
        broadcast: TcpBroadcastAdapter | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._bot = bot
        self._broadcast = broadcast
        self._bot_started = False

    @property
    def bot(self) -> TelegramBot:
        return self._bot

    async def start(self) -> None:
        if self._broadcast is not None:
            try:
                await self._broadcast.start()
                startup_event(
                    logger,
                    "broadcast",
                    status="ok",
                    agent=self._agent_id,
                    role=self._broadcast.role,
                    host=self._broadcast.host,
                    port=self._broadcast.port,
                )
            except Exception as exc:  # noqa: BLE001 — el bot arranca aunque el LAN no
                startup_event(
                    logger,
                    "broadcast",
                    status="error",
                    agent=self._agent_id,
                    reason=f"el puerto queda cerrado y ningún cliente podrá conectarse: {exc}",
                )

        app = self._bot.application
        await app.initialize()
        await app.start()
        self._bot_started = True
        await self._bot.setup_commands()
        await self._bot.verificar_bot_username()
        await self._bot.subscribe_broadcast_trigger()
        await self._bot.announce_back_online()
        updater = app.updater
        assert updater is not None, "PTB Application sin updater — config inesperada"
        await updater.start_polling(drop_pending_updates=False)
        startup_event(logger, "telegram_bot", status="ok", agent=self._agent_id, mode="polling")

    async def stop(self) -> None:
        if self._bot_started:
            app = self._bot.application
            updater = app.updater
            if updater is not None and updater.running:
                await updater.stop()
            if app.running:
                await app.stop()
            await app.shutdown()
            self._bot_started = False
        if self._broadcast is not None:
            try:
                await self._broadcast.stop()
            except Exception as exc:  # noqa: BLE001
                logger.error("Error deteniendo el broadcast de '%s': %s", self._agent_id, exc)
