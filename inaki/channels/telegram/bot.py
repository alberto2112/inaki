"""
TelegramBot — adaptador inbound para Telegram.

Un bot por agente. Se levanta solo si el agente tiene channels.telegram.token en su config.
Autorización por contexto (``TelegramAuth.is_authorized``):
- Privados: el user_id debe estar en allowed_user_ids (lista vacía = todos).
- Grupos: el chat_id debe estar en allowed_chat_ids (lista vacía = no responde en
  grupos); allowed_user_ids NO aplica en grupos.
Despacha según el behavior configurado (listen / mention / autonomous).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import Update
from telegram.error import BadRequest, NetworkError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from inaki.channels.telegram.auth import TelegramAuth
from inaki.channels.telegram.broadcast.egress import BroadcastEgress
from inaki.channels.telegram.broadcast.ingress import BroadcastIngress
from inaki.channels.telegram.broadcast.port import BroadcastEmitter, BroadcastReceiver
from inaki.channels.telegram.commands import SlashCommands
from inaki.channels.telegram.group_flow import GroupFlow
from inaki.channels.telegram.media import MediaHandlers
from inaki.channels.telegram.message_mapper import (
    _TIPOS_GRUPO,
    send_caption_or_plain,
    send_html_or_plain,
    telegram_update_to_input,
)
from inaki.channels.telegram.ports import TelegramBotPorts, TelegramBotSettings
from inaki.channels.telegram.rate_limit import GroupRateLimit
from inaki.channels.telegram.reactions import Reactions
from inaki.channels.telegram.turn import TurnRunner

logger = logging.getLogger(__name__)


# Delay aleatorio antes de flushar el buffer de grupo al LLM. Durante esta ventana,
# nuevos mensajes (de Telegram o broadcasts de otros bots) se acumulan en el historial
# y se procesan todos juntos en un único turno. Module-level para override en tests.
GROUP_RESPONSE_DELAY_MIN_SEC = 7.0
GROUP_RESPONSE_DELAY_MAX_SEC = 21.0

# Aviso que el bot manda al volver de un periodo offline, a cada chat privado que
# le escribió mientras estaba caído. Solo emojis: universal, sin idioma de base.
BACK_ONLINE_NOTICE = "👋🤖"

# Throttle entre avisos de arranque. Telegram tolera ~30 msg/s globales (1/s al
# MISMO chat), pero mandamos espaciado 1s para no arriesgar un 429 si hay muchos
# chats. Module-level para override en tests. Va ENTRE envíos: con un solo chat
# no se espera.
BACK_ONLINE_NOTICE_DELAY_SEC = 1.0


class TelegramBot:
    """Bot de Telegram de un agente — un agregado que COMPONE sus colaboradores.

    Este módulo conserva lo que es del bot como pieza de PTB: construir el
    ``Application``, registrar handlers, el error handler global, el routing
    del texto plano (``_handle_message``), la fachada ``send_*`` y el aviso de
    vuelta online. Todo lo demás son objetos con constructor explícito, cada
    uno recibiendo SOLO lo que usa (grafo acíclico, en orden de construcción):

    - ``TelegramAuth`` (``auth.py``) — la matriz de autorización.
    - ``Reactions`` (``reactions.py``) — 👀/👎 opcionales.
    - ``GroupRateLimit`` (``rate_limit.py``) — la política de rate limit: el único
      estado que ``/ratelimit`` muta en runtime.
    - ``TurnRunner`` (``turn.py``) — correr el agente y entregar la respuesta.
    - ``GroupFlow`` (``group_flow.py``) — routing por behavior + buffer-delay-coalesce.
    - ``BroadcastIngress`` (``broadcast/ingress.py``) — el trigger del LAN.
    - ``MediaHandlers`` (``media.py``) — fotos, álbumes, voz, video, documentos.
    - ``SlashCommands`` (``commands.py``) — el panel del operador.
    """

    def __init__(
        self,
        settings: TelegramBotSettings,
        ports: TelegramBotPorts,
        broadcast_emitter: BroadcastEmitter | None = None,
        broadcast_receiver: BroadcastReceiver | None = None,
        rate_limiter=None,
        reloader=None,
    ) -> None:
        self._settings = settings
        self._ports = ports

        # El bloque llega validado y tipado (``TelegramChannelSettings``): el
        # schema ya aplicó los defaults y el builder resolvió las herencias.
        tg_cfg = settings.telegram
        if not tg_cfg.token:
            raise ValueError(f"Agente '{settings.id}': channels.telegram.token no configurado")

        # Timeouts de red por encima de los defaults de python-telegram-bot (5s
        # connect/read/write, 1s pool). En una Pi 5 sobre red doméstica esos 5s
        # son apretados y disparan ``TimedOut`` varias veces al día. Subirlos ataca
        # la causa raíz: que el timeout casi no ocurra. El reintento seguro de
        # ``message_mapper`` cubre lo que igual falle. (No tocamos los get_updates_*:
        # el long-polling tiene su propia cadencia.)
        self._app = (
            Application.builder()
            .token(tg_cfg.token)
            .concurrent_updates(True)
            .connect_timeout(10.0)
            .read_timeout(20.0)
            .write_timeout(20.0)
            .pool_timeout(5.0)
            .build()
        )

        # Política de respuesta en grupos. Cuelga de ``groups`` y aplica con o sin
        # broadcast (migración groups-vs-broadcast). Los delays sin declarar caen
        # al default del módulo: la constante es del adapter, así que la resuelve
        # el adapter (el VO los deja en None).
        grupos = tg_cfg.groups
        self._behavior: str = grupos.behavior
        min_delay = (
            grupos.min_delay if grupos.min_delay is not None else GROUP_RESPONSE_DELAY_MIN_SEC
        )
        max_delay = (
            grupos.max_delay if grupos.max_delay is not None else GROUP_RESPONSE_DELAY_MAX_SEC
        )

        # --- colaboradores, en orden de dependencia ---
        self._auth = TelegramAuth(tg_cfg.allowed_user_ids, tg_cfg.allowed_chat_ids)
        self._reactions = Reactions(private=tg_cfg.reactions, groups=grupos.reactions)
        self._rate_limit = GroupRateLimit(
            rate_limiter,
            agent_id=settings.id,
            max_count=grupos.rate_limiter,
            window_seconds=grupos.rate_limiter_window,
        )
        # Emisión al broadcast: UNA política (``BroadcastEgress``) compartida con el
        # outbound del canal. Los handlers solo declaran QUÉ evento corresponde a cada flujo.
        self._egress = BroadcastEgress(broadcast_emitter, settings.id, tg_cfg.emit)
        self._turns = TurnRunner(
            ports=ports,
            app=self._app,
            agent_id=settings.id,
            behavior=self._behavior,
            broadcast_receiver=broadcast_receiver,
            egress=self._egress,
            reactions=self._reactions,
        )
        self._groups = GroupFlow(
            history=ports.history,
            agent_id=settings.id,
            behavior=self._behavior,
            bot_username=grupos.bot_username,
            min_delay=min_delay,
            max_delay=max_delay,
            reactions=self._reactions,
            rate_limit=self._rate_limit,
            turns=self._turns,
        )
        self._ingress = BroadcastIngress(
            receiver=broadcast_receiver,
            history=ports.history,
            agent_id=settings.id,
            behavior=self._behavior,
            auth=self._auth,
            rate_limit=self._rate_limit,
            groups=self._groups,
        )
        self._media = MediaHandlers(
            ports=ports,
            settings=settings,
            auth=self._auth,
            reactions=self._reactions,
            turns=self._turns,
            groups=self._groups,
            egress=self._egress,
        )
        self._commands = SlashCommands(
            ports=ports,
            settings=settings,
            app=self._app,
            auth=self._auth,
            rate_limit=self._rate_limit,
            reloader=reloader,
        )

        cmd = self._commands
        self._app.add_handler(CommandHandler("start", cmd.cmd_start))
        self._app.add_handler(CommandHandler("consolidate", cmd.cmd_consolidate))
        self._app.add_handler(CommandHandler("reconcile", cmd.cmd_reconcile))
        self._app.add_handler(CommandHandler("stop", cmd.cmd_stop))
        self._app.add_handler(CommandHandler("clear", cmd.cmd_clear))
        self._app.add_handler(CommandHandler("clear_all", cmd.cmd_clear_all))
        self._app.add_handler(CommandHandler("new", cmd.cmd_new))
        self._app.add_handler(CommandHandler("help", cmd.cmd_help))
        self._app.add_handler(CommandHandler("scheduler", cmd.cmd_scheduler))
        self._app.add_handler(CommandHandler("chatid", cmd.cmd_chatid))
        self._app.add_handler(CommandHandler("ratelimit", cmd.cmd_ratelimit))
        self._app.add_handler(CommandHandler("reload", cmd.cmd_reload))
        media = self._media
        # Handlers de voz ANTES del de texto (el dispatcher de python-telegram-bot
        # evalúa handlers en orden de registro). SIEMPRE registrados: el flag
        # ``voice_enabled`` controla si transcribir, no si persistir el file_id.
        self._app.add_handler(MessageHandler(filters.VOICE, media.handle_voice))
        self._app.add_handler(MessageHandler(filters.AUDIO, media.handle_voice))
        self._app.add_handler(MessageHandler(filters.VIDEO_NOTE, media.handle_voice))
        # Handler de fotos — antes del de texto para que PHOTO tenga prioridad.
        self._app.add_handler(MessageHandler(filters.PHOTO, media.handle_photo))
        # Documentos y videos: handlers MUDOS — sólo persisten file_id para que
        # el LLM pueda recuperarlos vía download_from_telegram. No responden ni
        # transcriben. Coherente con cómo se manejaban hoy los álbumes.
        self._app.add_handler(MessageHandler(filters.VIDEO, media.handle_silent_media))
        self._app.add_handler(MessageHandler(filters.Document.ALL, media.handle_silent_media))
        self._app.add_handler(MessageHandler(filters.LOCATION, self._handle_message))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

        # Error handler global. Telegram puede fallar por red (TimedOut /
        # ConnectTimeout) en CUALQUIER reply. Sin esto, PTB loguea el traceback
        # crudo ("No error handlers are registered") y deja el update sin
        # confirmar → tras un restart se re-entrega y vuelve a fallar (el bot
        # "se queda bobo"). Lo centralizamos acá en vez de envolver cada
        # reply_text uno por uno (evita la explosión N×M de handlers).
        self._app.add_error_handler(self._on_error)

    @property
    def application(self) -> Application:
        """El ``Application`` de PTB: el canal gobierna su ciclo de vida desde afuera."""
        return self._app

    # --- ciclo de vida: lo invoca ``TelegramChannel.start`` ---

    async def setup_commands(self) -> None:
        await self._commands.setup_commands()

    async def verificar_bot_username(self) -> None:
        await self._groups.resolve_bot_username(self._app.bot)

    async def subscribe_broadcast_trigger(self) -> None:
        await self._ingress.subscribe()

    async def announce_back_online(self) -> None:
        """Aviso 'online' a los chats con backlog."""
        await self._announce_back_online(self._app)

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Maneja excepciones no capturadas de cualquier handler de Telegram.

        Decisión de diseño: si el error es de RED con Telegram, el canal está
        caído — responder por él sería otro fallo a manejar (otro TimedOut). Solo
        registramos en el journal (stderr → systemd) y seguimos; el bot NO se
        queda bobo por un blip de red ni vomita un traceback crudo.

        ``BadRequest`` hereda de ``NetworkError`` pero NO es un blip de red: es un
        request malformado (bug nuestro). Lo dejamos caer al log de ERROR completo
        junto con cualquier otra excepción inesperada, para que quede visible.
        """
        err = context.error
        if isinstance(err, NetworkError) and not isinstance(err, BadRequest):
            logger.warning(
                "Telegram '%s': error de red transitorio con Telegram, update ignorado: %s",
                self._settings.id,
                err,
            )
            return
        logger.error(
            "Telegram '%s': error no manejado procesando un update: %s",
            self._settings.id,
            err,
            exc_info=err,
        )

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.message
        if user is None or message is None:
            return
        if not self._auth.is_authorized(update):
            logger.warning(
                "Mensaje rechazado de user_id=%s chat_id=%s (no autorizado)",
                user.id,
                message.chat.id,
            )
            return

        user_input = telegram_update_to_input(update)
        if not user_input:
            return

        chat_type = message.chat.type
        if chat_type in _TIPOS_GRUPO:
            await self._groups.handle_message(update, user_input, chat_type)
        else:
            await self._reactions.react(update, "👀")
            await self._turns.run(update, user_input, chat_type=chat_type)

    # --- fachada de salida: el BORDE del transporte ---

    async def send_message(self, chat_id: int, text: str) -> None:
        """Envía un mensaje proactivo fuera del contexto de un handler.

        Es la ÚNICA salida de texto para todo lo que no es una respuesta
        conversacional: sinks del scheduler (`channel_send`), intermedios en vivo
        del tool loop, resultados de delegación en background y
        `TelegramChannelOutbound`. Todos resuelven este bot vía
        `get_telegram_bot` y terminan acá.

        Por eso el renderizado markdown → HTML vive en ESTE borde y no en cada
        call-site: un camino de salida nuevo nace formateado, troceado a 4096 y
        con fallback a texto plano, sin tener que acordarse de nada.
        """
        await send_html_or_plain(
            lambda texto, pm: self._app.bot.send_message(
                chat_id=chat_id, text=texto, parse_mode=pm
            ),
            text,
        )

    async def send_photo(
        self,
        chat_id: int,
        photo: Any,
        caption: str | None = None,
    ) -> None:
        """Envía una foto a un chat. ``photo`` puede ser URL, path local o file-like."""
        await send_caption_or_plain(
            lambda texto, pm: self._app.bot.send_photo(
                chat_id=chat_id, photo=photo, caption=texto, parse_mode=pm
            ),
            caption,
            media=photo,
        )

    async def send_audio(
        self,
        chat_id: int,
        audio: Any,
        caption: str | None = None,
    ) -> None:
        await send_caption_or_plain(
            lambda texto, pm: self._app.bot.send_audio(
                chat_id=chat_id, audio=audio, caption=texto, parse_mode=pm
            ),
            caption,
            media=audio,
        )

    async def send_video(
        self,
        chat_id: int,
        video: Any,
        caption: str | None = None,
    ) -> None:
        await send_caption_or_plain(
            lambda texto, pm: self._app.bot.send_video(
                chat_id=chat_id, video=video, caption=texto, parse_mode=pm
            ),
            caption,
            media=video,
        )

    async def send_document(
        self,
        chat_id: int,
        document: Any,
        caption: str | None = None,
    ) -> None:
        await send_caption_or_plain(
            lambda texto, pm: self._app.bot.send_document(
                chat_id=chat_id, document=document, caption=texto, parse_mode=pm
            ),
            caption,
            media=document,
        )

    async def send_media_group(
        self,
        chat_id: int,
        media: list,
    ) -> None:
        await self._app.bot.send_media_group(chat_id=chat_id, media=media)

    async def _announce_back_online(self, app: Application) -> None:
        """Al arrancar, avisa a cada chat privado que escribió mientras estábamos caídos.

        Mientras el daemon está offline, Telegram acumula los updates no
        confirmados. NO los procesamos: reproducir N turnos de LLM al despertar
        dispararía hasta 256 turnos concurrentes (``concurrent_updates``), sin
        rate-limit global del provider — una ráfaga capaz de degradar el
        servicio en cada reinicio. En vez de eso solo mandamos un aviso liviano
        (cero LLM) y dejamos que el usuario reenvíe lo que importe.

        Drenamos la cola, confirmamos el offset para que el updater NO la
        re-entregue (con ``drop_pending_updates=False`` el backlog se descartaría
        solo si lo confirmamos acá), juntamos los chats privados autorizados que
        tenían algo pendiente y les mandamos ``BACK_ONLINE_NOTICE`` — uno por
        chat, deduplicado. Los grupos quedan fuera a propósito: anunciarse ahí
        sería ruido para todos los miembros.

        Lo invoca ``TelegramChannel.start`` entre ``Application.start()`` y
        ``updater.start_polling``: la app ya está inicializada pero el updater aún
        no arrancó, así que ``get_updates`` no compite con el long-polling. NO se
        usa el hook ``post_init`` de PTB: el lifecycle es manual e ``initialize()``
        NO dispara ``post_init`` (solo ``run_polling``/``run_webhook``, que no usamos).
        """
        try:
            pending = await app.bot.get_updates(timeout=0, limit=100)
        except Exception:  # pragma: no cover - Telegram/red caído al arrancar
            logger.warning("No se pudo drenar el backlog de Telegram al arrancar", exc_info=True)
            return

        if not pending:
            return

        # Confirmar TODOS los pendientes para que el polling no los re-entregue.
        # Si llegó un update nuevo entre ambas llamadas, queda sin confirmar y el
        # updater lo procesará normalmente — no se pierde.
        await app.bot.get_updates(offset=pending[-1].update_id + 1, timeout=0)

        # Solo chats PRIVADOS autorizados. ``is_authorized`` ya distingue grupo
        # vs privado y aplica ``allowed_user_ids``; acá además exigimos privado.
        chats_a_avisar: set[int] = set()
        for upd in pending:
            chat = upd.effective_chat
            if chat is None or chat.type in _TIPOS_GRUPO:
                continue
            if not self._auth.is_authorized(upd):
                continue
            chats_a_avisar.add(chat.id)

        if not chats_a_avisar:
            return

        # Orden determinístico + envío uno a uno, espaciado para respetar el
        # rate limit de Telegram. El sleep va ENTRE envíos (no antes del primero
        # ni después del último): un solo chat no agrega latencia al arranque.
        chats = sorted(chats_a_avisar)
        logger.info(
            "Backlog de Telegram: %d updates pendientes, aviso 'online' a %d chat(s) privado(s)",
            len(pending),
            len(chats),
        )
        for i, chat_id in enumerate(chats):
            if i > 0:
                await asyncio.sleep(BACK_ONLINE_NOTICE_DELAY_SEC)
            try:
                await app.bot.send_message(chat_id=chat_id, text=BACK_ONLINE_NOTICE)
            except Exception:  # pragma: no cover - un chat bloqueado/borrado no aborta el resto
                logger.warning("No se pudo avisar 'online' al chat_id=%s", chat_id, exc_info=True)
