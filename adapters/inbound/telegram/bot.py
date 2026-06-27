"""
TelegramBot — adaptador inbound para Telegram.

Un bot por agente. Se levanta solo si el agente tiene channels.telegram.token en su config.
Autorización por contexto (``_is_authorized``):
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
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from adapters.inbound.telegram.message_mapper import (
    _TIPOS_GRUPO,
    _safe_optional_str,
    compose_sender_identity,
    send_html_or_plain,
    telegram_update_to_input,
)
from adapters.inbound.turn_dispatch import dispatch_inbound_turn
from adapters.outbound.intermediate_sinks.telegram_live import TelegramLiveIntermediateSink
from core.domain.value_objects.channel_context import ChannelContext
from core.ports.outbound.broadcast_port import BroadcastEmitter, BroadcastReceiver
from adapters.inbound.telegram.broadcast import TelegramBroadcastMixin
from adapters.inbound.telegram.commands import TelegramCommandsMixin
from adapters.inbound.telegram.group_flow import TelegramGroupFlowMixin
from adapters.inbound.telegram.media import TelegramMediaMixin
from adapters.inbound.telegram.ports import TelegramBotPorts, TelegramBotSettings

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


class TelegramBot(
    TelegramCommandsMixin,
    TelegramMediaMixin,
    TelegramGroupFlowMixin,
    TelegramBroadcastMixin,
):
    """Bot de Telegram de un agente — composición de mixins por responsabilidad.

    Este módulo conserva el wiring (__init__, registro de handlers), el auth,
    el pipeline de turno privado (``_run_pipeline``), las reacciones y la
    fachada ``send_*``. El resto vive en módulos hermanos:

    - ``commands.py``   — comandos slash (/start, /scheduler, /ratelimit, ...).
    - ``media.py``      — fotos, álbumes, voz, video, documentos y file_ids.
    - ``group_flow.py`` — routing por behavior + buffer-delay-coalesce de grupos.
    - ``broadcast.py``  — emisión de eventos al LAN y trigger de ingress.

    Todo el estado se inicializa ACÁ; cada mixin declara el slice que consume
    en sus anotaciones de clase (contrato verificado por mypy).
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
        self._broadcast_emitter = broadcast_emitter
        self._broadcast_receiver = broadcast_receiver
        self._rate_limiter = rate_limiter
        # DaemonReloader compartido — lo inyecta el daemon runner al levantar el bot.
        # Permite que el handler /reload cierre y reabra todos los canales del daemon.
        # Opcional: en tests o arranques sueltos puede ser None y /reload responde sin efecto.
        self._reloader = reloader

        tg_cfg = settings.telegram
        self._token: str = tg_cfg.get("token", "")
        self._allowed_ids: list[str] = [str(uid) for uid in tg_cfg.get("allowed_user_ids", [])]
        self._reactions: bool = tg_cfg.get("reactions", False)
        self._voice_enabled: bool = tg_cfg.get("voice_enabled", True)

        self._allowed_chat_ids: list[str] = [str(cid) for cid in tg_cfg.get("allowed_chat_ids", [])]

        # Config específica de grupos: timing/reacciones + política de respuesta
        # (behavior, bot_username, rate_limiter). Soporta Pydantic model o dict crudo.
        groups_raw = tg_cfg.get("groups") or {}
        if hasattr(groups_raw, "model_dump"):
            groups_dict: dict = groups_raw.model_dump()
        elif isinstance(groups_raw, dict):
            groups_dict = groups_raw
        else:
            groups_dict = {}

        # Política de respuesta en grupos. Antes vivía en el bloque ``broadcast``,
        # lo que obligaba a levantar el transporte TCP solo para configurarla; ahora
        # cuelga de ``groups`` y aplica con o sin broadcast (migración groups-vs-broadcast).
        self._behavior: str = groups_dict.get("behavior", "mention")
        self._bot_username: str | None = groups_dict.get("bot_username")
        self._rate_limit_max: int = int(groups_dict.get("rate_limiter", 5))
        # Defaults preservados desde config para soportar `/ratelimit reset`.
        # Las mutaciones en runtime (vía comando) NO se persisten — al reiniciar
        # el daemon se vuelven a leer estos valores.
        self._rate_limit_max_default: int = self._rate_limit_max
        self._rate_limit_window_default: int = int(groups_dict.get("rate_limiter_window", 30))

        # Config de broadcast (transporte TCP): el bot solo necesita los flags
        # ``emit.*``. La topología (port/remote/auth) la consume el container al
        # wirear el adapter — el bot no la lee.
        broadcast_raw = tg_cfg.get("broadcast") or {}
        if hasattr(broadcast_raw, "model_dump"):
            broadcast_dict: dict = broadcast_raw.model_dump()
        elif isinstance(broadcast_raw, dict):
            broadcast_dict = broadcast_raw
        else:
            broadcast_dict = {}

        # Flags por event_type para emisión al canal de broadcast.
        # Defaults: solo assistant_response activo (backward-compat).
        emit_dict: dict = broadcast_dict.get("emit") or {}
        if not isinstance(emit_dict, dict):
            emit_dict = {}
        self._emit_flags: dict[str, bool] = {
            "assistant_response": bool(emit_dict.get("assistant_response", True)),
            "user_input_voice": bool(emit_dict.get("user_input_voice", False)),
            "user_input_photo": bool(emit_dict.get("user_input_photo", False)),
        }

        min_delay_cfg = groups_dict.get("min_delay_response")
        max_delay_cfg = groups_dict.get("max_delay_response")
        self._group_min_delay: float = (
            float(min_delay_cfg) if min_delay_cfg is not None else GROUP_RESPONSE_DELAY_MIN_SEC
        )
        self._group_max_delay: float = (
            float(max_delay_cfg) if max_delay_cfg is not None else GROUP_RESPONSE_DELAY_MAX_SEC
        )

        # reactions específico de grupos: si está seteado override, sino hereda del padre.
        reactions_override = groups_dict.get("reactions")
        self._group_reactions: bool = (
            bool(reactions_override) if reactions_override is not None else self._reactions
        )

        # Tasks de flush por chat_id. Cada chat tiene a lo sumo uno corriendo:
        # mientras está vivo, los mensajes que lleguen se acumulan en el historial
        # vía record_user_message y se procesan todos juntos cuando el delay vence.
        self._pending_tasks: dict[str, asyncio.Task] = {}

        # Último emisor humano por chat_id (grupos). Se actualiza en cada paso por
        # ``_handle_group_message`` y se lee en ``_run_group_pipeline`` para resolver
        # ``{{CHANNEL.SENDER}}/USERNAME/FIRST_NAME/LAST_NAME}}`` al flushear el buffer.
        # Heurística: el más reciente del batch gana. Se persiste in-memory; un
        # restart del daemon lo pierde (mismo trade-off que ``_pending_tasks``).
        # No se limpia tras el flush — la próxima ronda lo sobreescribe, y mientras
        # tanto refleja "la última persona que habló en este chat".
        self._last_group_sender: dict[str, dict[str, str | None]] = {}

        # Dedup de álbumes ya procesados (media_group_id). Telegram entrega un
        # álbum como N mensajes (uno por foto); solo el primero dispara el turno
        # coalescido, los demás solo persisten. Acotado para no crecer sin fin.
        self._albums_seen: dict[str, None] = {}

        if not self._token:
            raise ValueError(f"Agente '{settings.id}': channels.telegram.token no configurado")

        self._app = Application.builder().token(self._token).concurrent_updates(True).build()
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("consolidate", self._cmd_consolidate))
        self._app.add_handler(CommandHandler("reconcile", self._cmd_reconcile))
        self._app.add_handler(CommandHandler("clear", self._cmd_clear))
        self._app.add_handler(CommandHandler("clear_all", self._cmd_clear_all))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(CommandHandler("scheduler", self._cmd_scheduler))
        self._app.add_handler(CommandHandler("chatid", self._cmd_chatid))
        self._app.add_handler(CommandHandler("ratelimit", self._cmd_ratelimit))
        self._app.add_handler(CommandHandler("reload", self._cmd_reload))
        # Handlers de voz ANTES del de texto (el dispatcher de python-telegram-bot
        # evalúa handlers en orden de registro). SIEMPRE registrados: el flag
        # ``voice_enabled`` controla si transcribir, no si persistir el file_id.
        self._app.add_handler(MessageHandler(filters.VOICE, self._handle_voice_message))
        self._app.add_handler(MessageHandler(filters.AUDIO, self._handle_voice_message))
        self._app.add_handler(MessageHandler(filters.VIDEO_NOTE, self._handle_voice_message))
        # Handler de fotos — antes del de texto para que PHOTO tenga prioridad.
        self._app.add_handler(MessageHandler(filters.PHOTO, self._handle_photo_message))
        # Documentos y videos: handlers MUDOS — sólo persisten file_id para que
        # el LLM pueda recuperarlos vía download_from_telegram. No responden ni
        # transcriben. Coherente con cómo se manejaban hoy los álbumes.
        self._app.add_handler(MessageHandler(filters.VIDEO, self._handle_silent_media))
        self._app.add_handler(MessageHandler(filters.Document.ALL, self._handle_silent_media))
        self._app.add_handler(MessageHandler(filters.LOCATION, self._handle_message))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

        # Error handler global. Telegram puede fallar por red (TimedOut /
        # ConnectTimeout) en CUALQUIER reply. Sin esto, PTB loguea el traceback
        # crudo ("No error handlers are registered") y deja el update sin
        # confirmar → tras un restart se re-entrega y vuelve a fallar (el bot
        # "se queda bobo"). Lo centralizamos acá en vez de envolver cada
        # reply_text uno por uno (evita la explosión N×M de handlers).
        self._app.add_error_handler(self._on_error)

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

    def _is_allowed(self, user_id: int) -> bool:
        if not self._allowed_ids:
            return True  # Lista vacía = todos permitidos
        return str(user_id) in self._allowed_ids

    def _is_allowed_chat(self, chat_id: int) -> bool:
        """Verifica si el chat_id del grupo está en la lista de permitidos.

        Lista vacía = el bot NO responde en grupos (solo chats privados).
        Solo aplica a mensajes grupales; los privados se filtran por ``_is_allowed``.
        """
        return str(chat_id) in self._allowed_chat_ids

    def _is_authorized(self, update: Update) -> bool:
        """Matriz de autorización por contexto del mensaje.

        - Grupo: autorizado solo si el ``chat_id`` está en ``allowed_chat_ids``.
          El filtro ``allowed_user_ids`` NO aplica — cualquier miembro de un
          grupo autorizado puede interactuar.
        - Privado: autorizado según ``allowed_user_ids`` (lista vacía = todos).
        - Update sin emisor o sin chat (defensivo): rechazado.

        Guardián único de los handlers de mensaje (texto, foto, voz, media). Los
        comandos slash NO lo usan: siguen siendo admin-only vía ``_is_allowed``.
        """
        chat = update.effective_chat
        user = update.effective_user
        if chat is None or user is None:
            return False
        if chat.type in _TIPOS_GRUPO:
            return self._is_allowed_chat(chat.id)
        return self._is_allowed(user.id)

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.message
        if user is None or message is None:
            return
        if not self._is_authorized(update):
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
        es_grupo = chat_type in _TIPOS_GRUPO

        if es_grupo:
            await self._handle_group_message(update, user_input, chat_type)
        else:
            # Chat privado: comportamiento original sin cambios.
            await self._set_reaction(update, "👀")
            await self._run_pipeline(update, user_input, chat_type=chat_type)

    async def _set_reaction(self, update: Update, emoji: str) -> None:
        """Envía una reacción al mensaje si `reactions` está activo. Silencia fallos.

        Aplica a chats privados y voice. Para reacciones en grupos usar
        ``_set_group_reaction`` que respeta el override ``groups.reactions``.
        """
        if not self._reactions:
            return
        message = update.message
        if message is None:
            return
        try:
            await message.set_reaction(emoji)
        except Exception:
            pass  # Reacciones opcionales — no deben bloquear el handler.

    async def _set_group_reaction(self, update: Update, emoji: str) -> None:
        """Reacción en grupos. Respeta ``channels.telegram.groups.reactions``
        si está seteado, hereda de ``channels.telegram.reactions`` si no."""
        if not self._group_reactions:
            return
        message = update.message
        if message is None:
            return
        try:
            await message.set_reaction(emoji)
        except Exception:
            pass

    async def _run_pipeline(
        self,
        update: Update,
        user_input: str | None,
        chat_type: str = "private",
        extra_sections: list[str] | None = None,
    ) -> None:
        """Ejecuta el agente con `user_input` (texto tipeado, transcripto o formateado de grupo).

        Centraliza el ciclo común: channel_context → extra_sections → live_sink →
        run_agent.execute → __SKIP__ check → reply HTML → broadcast egress →
        reacción ✅/❌ → limpiar contexto al final.

        Args:
            update: Update de Telegram.
            user_input: Texto ya formateado para el LLM (con prefijo de usuario si es grupo).
                Si es ``None``, ``run_agent.execute`` deriva la query del trailing batch
                ``role=user`` del historial (modo history-derived). Usado por el handler
                de fotos cuando el placeholder ya fue enriquecido vía ``update_message_content``.
            chat_type: Tipo de chat (``"private"``, ``"group"``, ``"supergroup"``, ``"channel"``).
            extra_sections: Secciones adicionales del system prompt (broadcast context,
                instrucción __SKIP__, etc.). Se pasan via ``set_extra_system_sections`` ANTES
                de invocar ``execute``.
        """
        chat = update.effective_chat
        user = update.effective_user
        message = update.message
        if chat is None or user is None or message is None:
            return
        chat_id = chat.id
        es_grupo = chat_type in _TIPOS_GRUPO
        secciones: list[str] = list(extra_sections or [])

        # Inyectar contexto de broadcast si hay receiver y es un grupo.
        if es_grupo and self._broadcast_receiver is not None:
            rendered = self._broadcast_receiver.render(str(chat_id))
            if rendered:
                secciones.insert(0, rendered)

        # Inyectar secciones adicionales en el use case ANTES de execute().
        secciones_no_vacias = [s for s in secciones if s]
        self._ports.run_agent.set_extra_system_sections(secciones_no_vacias)

        # Identidad del remitente — se puebla siempre que haya un ``update.message``
        # concreto detrás del turno, ya sea chat privado, mention/respuesta dirigida
        # al bot en grupo, o voice/foto en grupo (estos no pasan por el buffer).
        # En todos esos casos hay UN único humano emisor que disparó este
        # ``execute()``, así que ``{{CHANNEL.SENDER}}/USERNAME/FIRST_NAME/LAST_NAME}}``
        # tienen valor unívoco.
        #
        # El path autonomous-flush de grupos (``_run_group_pipeline``) cubre el
        # caso "texto plano sin mention en grupo": ahí el sender se resuelve a
        # partir de ``self._last_group_sender[chat_id]`` (heurística: último
        # emisor humano del batch).
        sender_name: str | None = None
        sender_username: str | None = None
        sender_first_name: str | None = None
        sender_last_name: str | None = None
        if update.message is not None:
            from_user = getattr(update.message, "from_user", None)
            if from_user is not None:
                sender_username = _safe_optional_str(getattr(from_user, "username", None))
                sender_first_name = _safe_optional_str(getattr(from_user, "first_name", None))
                sender_last_name = _safe_optional_str(getattr(from_user, "last_name", None))
            sender_name = _safe_optional_str(compose_sender_identity(update.message))

        turn_ctx = ChannelContext(
            channel_type="telegram",
            user_id=str(user.id),
            chat_id=str(chat_id),
            sender_name=sender_name,
            username=sender_username,
            first_name=sender_first_name,
            last_name=sender_last_name,
        )
        # En grupos NO usamos intermediate_sink: los intermedios del LLM (texto que
        # acompaña tool_calls) se emitirían directo al chat vía sink y NO se incluirían
        # en el ``response`` final → el broadcast saldría con texto vacío/residual y
        # los otros bots del grupo no verían la respuesta. Alineado con _run_group_pipeline.
        live_sink: TelegramLiveIntermediateSink | None = (
            None if es_grupo else TelegramLiveIntermediateSink(bot=self, chat_id=chat_id)
        )
        # In-flight-message-injection: para chats PRIVADOS, si ya hay un turno
        # corriendo en este scope, persistimos el mensaje y ACK rápido. El loop
        # en curso drenará el mensaje entre iteraciones via history.db.
        # Para GRUPOS mantenemos el flow legacy: ya tienen su propio buffer-delay
        # vía _schedule_group_flush + record_user_message, y la inyección
        # in-flight no aplica (SCN-IFI-13/14 del spec).
        agent_id = self._ports.run_agent.get_agent_info().id
        scope = (agent_id, "telegram", str(chat_id))
        skip_marker_value = "__SKIP__" if (self._behavior == "autonomous" and es_grupo) else None

        # Si es grupo o si user_input is None (modo history-derived: foto enriquecida)
        # → saltar el branch in-flight y caer en el flow legacy.
        use_inflight_routing = not es_grupo and user_input is not None

        # El scope (channel, chat_id) se deriva de turn_ctx dentro de execute —
        # una sola fuente de verdad, sin estado compartido entre turnos.
        async def _ejecutar_turno() -> str:
            return await self._ports.run_agent.execute(
                user_input,
                intermediate_sink=live_sink,
                ctx=turn_ctx,
                skip_marker=skip_marker_value,
            )

        try:
            if use_inflight_routing:
                # use_inflight_routing implica user_input is not None (ver
                # asignación arriba); asertamos para narrowear el tipo a str.
                assert user_input is not None
                result = await dispatch_inbound_turn(
                    scope_registry=self._ports.scope_registry,
                    run_agent=self._ports.run_agent,
                    scope=scope,
                    message=user_input,
                    execute=_ejecutar_turno,
                )
                if not result.executed:
                    # Scope ocupado por otro turno: el helper ya persistió el
                    # mensaje; acá solo va el ACK rápido al chat.
                    if update.message is not None:
                        await update.message.reply_text(result.reply)
                    return
                response = result.reply
            else:
                # Flow legacy (grupo o history-derived): sin slot, turno directo.
                response = await _ejecutar_turno()

            # Verificar marcador __SKIP__ — solo aplica en modo autónomo en grupos.
            # Detección TOLERANTE: el marcador puede aparecer en cualquier parte
            # de la respuesta (los LLMs suelen agregar pre/post-amble incluso ante
            # la instrucción "respondé EXACTAMENTE con __SKIP__"). Cualquier
            # ocurrencia suprime el envío al chat y el broadcast. El use case
            # aplica la misma regla para descartar la persistencia.
            if self._behavior == "autonomous" and es_grupo and "__SKIP__" in response.upper():
                logger.debug(
                    "autonomous_skip detectado (agent=%s, chat_id=%s)",
                    self._settings.id,
                    chat_id,
                )
                return

            await send_html_or_plain(
                lambda text, pm: message.reply_text(text, parse_mode=pm), response
            )

            # Emitir broadcast DESPUÉS del reply, solo para grupos, fire-and-forget.
            # Gated por broadcast.emit.assistant_response (default true).
            if es_grupo:
                asyncio.ensure_future(
                    self._emit_event(
                        event_type="assistant_response",
                        chat_id=str(chat_id),
                        content=response,
                    )
                )

        except Exception as exc:
            # Blip de red transitorio entregando la respuesta (TimedOut /
            # ConnectTimeout). Para acá el turno YA corrió y la respuesta YA está
            # persistida en history.db (RunAgentUseCase persiste ANTES de devolver):
            # mandar otro reply_text sería otro fallo y le mostraría al usuario
            # "Error: Timed out" sobre contenido que SÍ se guardó. Coherente con
            # _on_error: logueamos a WARNING y seguimos. ``BadRequest`` hereda de
            # ``NetworkError`` pero es un request malformado (bug nuestro) → cae al
            # manejo de error real de abajo.
            if isinstance(exc, NetworkError) and not isinstance(exc, BadRequest):
                logger.warning(
                    "Telegram '%s': error de red entregando la respuesta "
                    "(ya persistida en history.db), se ignora: %s",
                    self._settings.id,
                    exc,
                )
                return
            logger.exception("Error procesando mensaje Telegram para '%s'", self._settings.id)
            await message.reply_text(f"Error: {exc}")
            await self._set_reaction(update, "👎")
        finally:
            # El slot del scope registry lo libera dispatch_inbound_turn en su
            # propio finally — acá solo queda la limpieza de extra_sections
            # para no contaminar el turno siguiente.
            self._ports.run_agent.set_extra_system_sections([])

    async def verificar_bot_username(self) -> None:
        """Obtiene y valida el username del bot contra la API de Telegram.

        Llama a ``get_me()`` UNA SOLA VEZ al arranque. No bloquea ni falla el startup:
        - Si ``bot_username`` no está en config → se auto-detecta para que los filtros funcionen.
        - Si el username real difiere del configurado → WARNING (no bloquea).
        - Si ``get_me()`` falla → WARNING (no bloquea).
        """
        try:
            me = await self._app.bot.get_me()
        except Exception as exc:
            logger.warning(
                "Telegram bot '%s': no se pudo obtener bot info via get_me(): %s",
                self._settings.id,
                exc,
            )
            return

        real_username = me.username  # puede ser None si el bot no tiene username

        if real_username is None:
            logger.warning(
                "Telegram bot '%s': get_me() devolvió username=None. "
                "Los filtros de reply y mención no funcionarán correctamente.",
                self._settings.id,
            )
            return

        if self._bot_username is None:
            self._bot_username = real_username
            logger.info(
                "Telegram bot '%s': bot_username auto-detectado: @%s",
                self._settings.id,
                real_username,
            )
            return

        if real_username.lower() != self._bot_username.lower():
            logger.warning(
                "Telegram bot '%s': bot_username en config ('%s') no coincide "
                "con el username real del bot ('@%s'). "
                "Actualizá groups.bot_username en la config para evitar fallos en mention detection.",
                self._settings.id,
                self._bot_username,
                real_username,
            )
        else:
            logger.info(
                "Telegram bot '%s': bot_username validado correctamente ('@%s')",
                self._settings.id,
                real_username,
            )

    async def send_message(self, chat_id: int, text: str) -> None:
        """Envía un mensaje proactivo fuera del contexto de un handler.

        Usado por `ChannelSenderAdapter` para triggers `channel_send` del
        scheduler. Delega en el bot interno de `python-telegram-bot`.
        """
        await self._app.bot.send_message(chat_id=chat_id, text=text)

    async def send_photo(
        self,
        chat_id: int,
        photo: Any,
        caption: str | None = None,
    ) -> None:
        """Envía una foto a un chat. ``photo`` puede ser URL, path local o file-like."""
        await self._app.bot.send_photo(chat_id=chat_id, photo=photo, caption=caption)

    async def send_audio(
        self,
        chat_id: int,
        audio: Any,
        caption: str | None = None,
    ) -> None:
        await self._app.bot.send_audio(chat_id=chat_id, audio=audio, caption=caption)

    async def send_video(
        self,
        chat_id: int,
        video: Any,
        caption: str | None = None,
    ) -> None:
        await self._app.bot.send_video(chat_id=chat_id, video=video, caption=caption)

    async def send_document(
        self,
        chat_id: int,
        document: Any,
        caption: str | None = None,
    ) -> None:
        await self._app.bot.send_document(chat_id=chat_id, document=document, caption=caption)

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

        Lo invoca el daemon (``_run_telegram_bot``) entre ``Application.start()`` y
        ``updater.start_polling``: la app ya está inicializada pero el updater aún
        no arrancó, así que ``get_updates`` no compite con el long-polling. NO se
        usa el hook ``post_init`` de PTB: el daemon maneja el lifecycle a mano con
        ``async with app`` e ``initialize()`` NO dispara ``post_init`` (solo lo
        hacen ``run_polling``/``run_webhook``, que el daemon no usa).
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

        # Solo chats PRIVADOS autorizados. ``_is_authorized`` ya distingue grupo
        # vs privado y aplica ``allowed_user_ids``; acá además exigimos privado.
        chats_a_avisar: set[int] = set()
        for upd in pending:
            chat = upd.effective_chat
            if chat is None or chat.type in _TIPOS_GRUPO:
                continue
            if not self._is_authorized(upd):
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
