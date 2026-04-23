"""
TelegramBot — adaptador inbound para Telegram.

Un bot por agente. Se levanta solo si el agente tiene channels.telegram.token en su config.
Valida que el user_id esté en allowed_user_ids (si la lista no está vacía).
Para grupos, también valida allowed_chat_ids y despacha según el behavior configurado
(listen / mention / autonomous).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from adapters.inbound.telegram.message_mapper import (
    detect_mention,
    hay_menciones,
    extract_audio_payload,
    format_group_message,
    format_response,
    telegram_update_to_input,
)
from adapters.outbound.intermediate_sinks.telegram_live import TelegramLiveIntermediateSink
from core.domain.entities.task import ScheduledTask
from core.domain.errors import TaskNotFoundError, TranscriptionError
from core.domain.value_objects.channel_context import ChannelContext
from core.ports.outbound.broadcast_port import BroadcastEmitter, BroadcastMessage, BroadcastReceiver
from infrastructure.config import AgentConfig
from infrastructure.container import AgentContainer

logger = logging.getLogger(__name__)

# Tipos de chat que Telegram considera "grupos" (no privados).
_TIPOS_GRUPO = {"group", "supergroup", "channel"}

# Jitter aleatorio aplicado antes de procesar cada broadcast recibido. Distribuye
# respuestas simultáneas entre bots para que el BroadcastBuffer pueda cruzar
# contexto y para romper ráfagas. Module-level para facilitar override en tests.
BROADCAST_TRIGGER_JITTER_MIN_SEC = 1.0
BROADCAST_TRIGGER_JITTER_MAX_SEC = 3.0


class TelegramBot:
    def __init__(
        self,
        agent_cfg: AgentConfig,
        container: AgentContainer,
        broadcast_emitter: BroadcastEmitter | None = None,
        broadcast_receiver: BroadcastReceiver | None = None,
        rate_limiter=None,
    ) -> None:
        self._agent_cfg = agent_cfg
        self._container = container
        self._broadcast_emitter = broadcast_emitter
        self._broadcast_receiver = broadcast_receiver
        self._rate_limiter = rate_limiter

        tg_cfg = agent_cfg.channels.get("telegram", {})
        self._token: str = tg_cfg.get("token", "")
        self._allowed_ids: list[str] = [str(uid) for uid in tg_cfg.get("allowed_user_ids", [])]
        self._reactions: bool = tg_cfg.get("reactions", False)
        self._voice_enabled: bool = tg_cfg.get("voice_enabled", True)

        # Config de broadcast: lista de chat_ids permitidos + behavior + bot_username
        self._allowed_chat_ids: list[str] = [str(cid) for cid in tg_cfg.get("allowed_chat_ids", [])]
        broadcast_raw = tg_cfg.get("broadcast") or {}
        if hasattr(broadcast_raw, "model_dump"):
            # Ya es un Pydantic model (Batch 5 en adelante)
            broadcast_dict: dict = broadcast_raw.model_dump()
        elif isinstance(broadcast_raw, dict):
            broadcast_dict = broadcast_raw
        else:
            broadcast_dict = {}

        self._behavior: str = broadcast_dict.get("behavior", "mention")
        self._bot_username: str | None = broadcast_dict.get("bot_username")
        self._rate_limit_max: int = int(broadcast_dict.get("rate_limiter", 5))

        if not self._token:
            raise ValueError(f"Agente '{agent_cfg.id}': channels.telegram.token no configurado")

        self._app = Application.builder().token(self._token).build()
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("consolidate", self._cmd_consolidate))
        self._app.add_handler(CommandHandler("clear", self._cmd_clear))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(CommandHandler("scheduler", self._cmd_scheduler))
        self._app.add_handler(CommandHandler("chatid", self._cmd_chatid))
        # Handlers de voz ANTES del de texto (el dispatcher de python-telegram-bot
        # evalúa handlers en orden de registro). Sólo se registran si el feature
        # flag está activo; con voice_enabled=False no se engancha ningún filtro.
        if self._voice_enabled:
            self._app.add_handler(MessageHandler(filters.VOICE, self._handle_voice_message))
            self._app.add_handler(MessageHandler(filters.AUDIO, self._handle_voice_message))
            self._app.add_handler(MessageHandler(filters.VIDEO_NOTE, self._handle_voice_message))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

    def _is_allowed(self, user_id: int) -> bool:
        if not self._allowed_ids:
            return True  # Lista vacía = todos permitidos
        return str(user_id) in self._allowed_ids

    def _is_allowed_chat(self, chat_id: int) -> bool:
        """Verifica si el chat_id del grupo está en la lista de permitidos.

        Lista vacía = sin restricción de grupo (todos los grupos autorizados).
        Solo aplica a mensajes grupales; los privados no pasan por esta verificación.
        """
        if not self._allowed_chat_ids:
            return True
        return str(chat_id) in self._allowed_chat_ids

    async def _cmd_chatid(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/chatid — responde con el ID del chat actual.

        Bypasea ``allowed_chat_ids`` para poder usarlo antes de agregar el grupo a la whitelist
        (bootstrap de configuración). Sin embargo, sigue respetando ``allowed_user_ids``:
        si el usuario no está autorizado, se ignora silenciosamente.

        Útil para obtener el ``chat_id`` de un grupo y agregarlo a ``allowed_chat_ids``.
        """
        if not self._is_allowed(update.effective_user.id):
            return

        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type
        logger.info(
            "/chatid invocado",
            extra={
                "user_id": update.effective_user.id,
                "chat_id": chat_id,
                "chat_type": chat_type,
            },
        )
        await update.message.reply_text(str(chat_id))

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update.effective_user.id):
            return
        await update.message.reply_text(
            f"Hola, soy {self._agent_cfg.name}. {self._agent_cfg.description}"
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update.effective_user.id):
            return
        await update.message.reply_text(
            "/consolidate — Extraer recuerdos del historial\n"
            "/clear — Limpiar historial sin archivar (igual que en CLI)\n"
            "/scheduler list — Listar tareas programadas\n"
            "/scheduler show <id> — Detalle de una tarea\n"
            "/scheduler enable <id> — Habilitar una tarea\n"
            "/scheduler disable <id> — Deshabilitar una tarea\n"
            "/start — Presentación\n"
            "/help — Este mensaje"
        )

    async def _cmd_consolidate(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update.effective_user.id):
            return
        await update.message.reply_text("Consolidando memoria...")
        try:
            result = await self._container.consolidate_memory.execute()
            await update.message.reply_text(result)
        except Exception as exc:
            await update.message.reply_text(f"Error: {exc}")

    async def _cmd_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Mismo comportamiento que `/clear` en CLI: borra el historial del agente."""
        if not self._is_allowed(update.effective_user.id):
            return
        try:
            await self._container.run_agent.clear_history()
            await update.message.reply_text("Historial limpiado.")
        except Exception as exc:
            logger.exception("Error en /clear Telegram para '%s'", self._agent_cfg.id)
            await update.message.reply_text(f"Error: {exc}")

    async def _cmd_scheduler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """`/scheduler {list|show|enable|disable} [id]` — gestión read-only/toggle de tareas."""
        if not self._is_allowed(update.effective_user.id):
            return

        uc = self._container.schedule_task
        if uc is None:
            await update.message.reply_text("El scheduler no está inicializado en este proceso.")
            return

        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Uso:\n"
                "/scheduler list\n"
                "/scheduler show <id>\n"
                "/scheduler enable <id>\n"
                "/scheduler disable <id>"
            )
            return

        sub = args[0].lower()

        if sub == "list":
            try:
                tasks = await uc.list_tasks()
            except Exception as exc:
                logger.exception("Error en /scheduler list para '%s'", self._agent_cfg.id)
                await update.message.reply_text(f"Error: {exc}")
                return
            if not tasks:
                await update.message.reply_text("No hay tareas programadas.")
                return
            await update.message.reply_text(self._format_task_list(tasks))
            return

        if sub in {"show", "enable", "disable"}:
            if len(args) < 2:
                await update.message.reply_text(f"Uso: /scheduler {sub} <id>")
                return
            try:
                task_id = int(args[1])
            except ValueError:
                await update.message.reply_text(f"ID inválido: {args[1]}")
                return

            try:
                if sub == "show":
                    task = await uc.get_task(task_id)
                    await update.message.reply_text(self._format_task_detail(task))
                elif sub == "enable":
                    await uc.enable_task(task_id)
                    await update.message.reply_text(f"Tarea {task_id} habilitada.")
                else:  # disable
                    await uc.disable_task(task_id)
                    await update.message.reply_text(f"Tarea {task_id} deshabilitada.")
            except TaskNotFoundError:
                await update.message.reply_text(f"Tarea {task_id} no encontrada.")
            except Exception as exc:
                logger.exception(
                    "Error en /scheduler %s %s para '%s'", sub, task_id, self._agent_cfg.id
                )
                await update.message.reply_text(f"Error: {exc}")
            return

        await update.message.reply_text(
            f"Sub-comando desconocido: {sub}. Usá list, show, enable o disable."
        )

    @staticmethod
    def _format_task_list(tasks: list[ScheduledTask]) -> str:
        lines = ["Tareas programadas:", ""]
        for t in tasks:
            flag = "✓" if t.enabled else "✗"
            next_run = t.next_run.isoformat() if t.next_run else "-"
            lines.append(
                f"{flag} [{t.id}] {t.name}\n"
                f"   kind={t.task_kind.value}, trigger={t.trigger_type.value}, next={next_run}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_task_detail(task: ScheduledTask) -> str:
        lines = [
            f"Tarea {task.id} — {task.name}",
            f"Descripción: {task.description or '-'}",
            f"Kind: {task.task_kind.value}",
            f"Trigger: {task.trigger_type.value}",
            f"Schedule: {task.schedule}",
            f"Enabled: {'sí' if task.enabled else 'no'}",
            f"Status: {task.status.value}",
            f"Next run: {task.next_run.isoformat() if task.next_run else '-'}",
            f"Last run: {task.last_run.isoformat() if task.last_run else '-'}",
        ]
        if task.executions_remaining is not None:
            lines.append(f"Executions remaining: {task.executions_remaining}")
        return "\n".join(lines)

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update.effective_user.id):
            logger.warning(
                "Mensaje rechazado de user_id=%s (no autorizado)",
                update.effective_user.id,
            )
            return

        user_input = telegram_update_to_input(update)
        if not user_input:
            return

        chat_type = update.message.chat.type
        es_grupo = chat_type in _TIPOS_GRUPO

        if es_grupo:
            await self._handle_group_message(update, user_input, chat_type)
        else:
            # Chat privado: comportamiento original sin cambios.
            await self._set_reaction(update, "👀")
            await self._run_pipeline(update, user_input, chat_type=chat_type)

    async def _handle_group_message(self, update: Update, user_input: str, chat_type: str) -> None:
        """Maneja mensajes de chats grupales según el behavior configurado.

        Flujo:
        1. Verificar allowed_chat_ids (si el chat_id no está en la lista y la lista no está vacía,
           solo se pasa si el usuario está en allowed_user_ids — igual que privado).
        2. Despachar por behavior:
           - listen → descarta silenciosamente.
           - mention → solo procesa si el bot está mencionado.
           - autonomous → siempre procesa; aplica rate limiter.
        3. Formatea el contenido con ``format_group_message`` antes de pasarlo al pipeline.
        """
        chat_id = update.effective_chat.id

        # Verificación de allowed_chat_ids para grupos:
        # Si el chat_id está en la whitelist, CUALQUIER usuario del grupo puede escribir.
        # Si NO está, se usa el fallback de allowed_user_ids (comportamiento privado).
        if self._allowed_chat_ids and not self._is_allowed_chat(chat_id):
            # Chat no whitelisted — ignorar silenciosamente para grupos.
            logger.debug(
                "Grupo no whitelisted ignorado (chat_id=%s, agent=%s)",
                chat_id,
                self._agent_cfg.id,
            )
            return

        behavior = self._behavior

        if behavior == "listen":
            # Modo escucha: recibe mensajes del grupo pero nunca responde.
            return

        # Filtro de mención explícita a otro bot.
        # Si bot_username está configurado y el mensaje menciona a alguien (cualquier behavior)
        # pero ninguna mención es para este bot → no participar.
        if self._bot_username and hay_menciones(update.message) and not detect_mention(
            update.message, self._bot_username
        ):
            logger.debug(
                "Mención a otro bot detectada, ignorando (agent=%s, chat_id=%s)",
                self._agent_cfg.id,
                chat_id,
            )
            return

        if behavior == "mention":
            # Solo responde si el bot está explícitamente mencionado.
            if not self._bot_username:
                logger.warning(
                    "behavior='mention' pero bot_username no configurado (agent=%s) — ignorando",
                    self._agent_cfg.id,
                )
                return
            if not detect_mention(update.message, self._bot_username):
                return

        # Para autonomous: verificar rate limiter antes de invocar el LLM.
        extra_sections: list[str] = []

        if behavior == "autonomous":
            # Sección de instrucción [SKIP] para modo autónomo.
            seccion_skip = (
                "## Modo autónomo\n"
                "Si después de leer el contexto considerás que no tenés nada útil que aportar "
                "al grupo, respondé EXACTAMENTE con `[SKIP]` (mayúsculas, entre corchetes, nada "
                "más). El sistema detecta ese marcador y no enviará nada al grupo."
            )
            extra_sections.append(seccion_skip)

            if self._rate_limiter is not None:
                breach = self._rate_limiter.check_and_increment(
                    self._agent_cfg.id,
                    str(chat_id),
                    self._rate_limit_max,
                )
                if breach is not None:
                    logger.debug(
                        "Rate limit alcanzado en grupo (agent=%s, chat_id=%s, counter=%d)",
                        self._agent_cfg.id,
                        chat_id,
                        breach.counter,
                    )
                    # No invocamos el LLM — solo registramos la señal de breach como telemetría.
                    return

        # Formatear el contenido con prefijo de remitente antes de pasar al pipeline.
        contenido_grupo = format_group_message(update.message)

        await self._set_reaction(update, "👀")
        await self._run_pipeline(
            update,
            contenido_grupo,
            chat_type=chat_type,
            extra_sections=extra_sections,
        )

    async def _handle_voice_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handler único para `voice`, `audio` y `video_note`.

        Flujo: allow → voice_enabled → 🔊 → descarga+mime → size-check →
        transcribir → reinyectar el texto en el mismo pipeline que `_handle_message`.
        """
        if not self._is_allowed(update.effective_user.id):
            logger.warning(
                "Audio rechazado de user_id=%s (no autorizado)",
                update.effective_user.id,
            )
            return

        if not self._voice_enabled:
            # Feature flag apagado: silencio total, ni reacción ni reply.
            return

        payload = await extract_audio_payload(update.message)
        if payload is None:
            return  # Defensa: ningún audio presente (no debería ocurrir por los filters).
        audio_bytes, mime, file_size = payload

        # Size-check: preferimos file_size de Telegram (antes de procesar),
        # con fallback al tamaño real descargado.
        max_mb = self._agent_cfg.transcription.max_audio_mb
        max_bytes = max_mb * 1024 * 1024
        effective_size = file_size or len(audio_bytes)
        if effective_size > max_bytes:
            logger.warning(
                "Audio demasiado grande para agente '%s': %d bytes > límite %d bytes",
                self._agent_cfg.id,
                effective_size,
                max_bytes,
            )
            await self._set_reaction(update, "❌")
            await update.message.reply_text(
                f"El audio es demasiado grande ({effective_size // (1024 * 1024)} MB). "
                f"Máximo permitido: {max_mb} MB."
            )
            return

        await self._set_reaction(update, "🔊")

        # Transcribir — errores del provider se reportan al usuario pero NO
        # corren el pipeline (sin texto no hay nada que ejecutar).
        try:
            transcribed = await self._container.transcription.transcribe(
                audio_bytes,
                mime,
                language=self._agent_cfg.transcription.language,
            )
        except TranscriptionError as exc:
            logger.warning("Transcripción fallida para agente '%s': %s", self._agent_cfg.id, exc)
            await update.message.reply_text(f"No pude transcribir el audio: {exc}")
            await self._set_reaction(update, "❌")
            return

        if not transcribed or not transcribed.strip():
            await update.message.reply_text("La transcripción vino vacía.")
            await self._set_reaction(update, "❌")
            return

        chat_type = update.message.chat.type if update.message else "private"
        await self._run_pipeline(update, transcribed, chat_type=chat_type)

    async def _set_reaction(self, update: Update, emoji: str) -> None:
        """Envía una reacción al mensaje si `reactions` está activo. Silencia fallos."""
        if not self._reactions:
            return
        try:
            await update.message.set_reaction(emoji)
        except Exception:
            pass  # Reacciones opcionales — no deben bloquear el handler.

    async def _run_pipeline(
        self,
        update: Update,
        user_input: str,
        chat_type: str = "private",
        extra_sections: list[str] | None = None,
    ) -> None:
        """Ejecuta el agente con `user_input` (texto tipeado, transcripto o formateado de grupo).

        Centraliza el ciclo común: channel_context → extra_sections → live_sink →
        run_agent.execute → [SKIP] check → reply HTML → broadcast egress →
        reacción ✅/❌ → limpiar contexto al final.

        Args:
            update: Update de Telegram.
            user_input: Texto ya formateado para el LLM (con prefijo de usuario si es grupo).
            chat_type: Tipo de chat (``"private"``, ``"group"``, ``"supergroup"``, ``"channel"``).
            extra_sections: Secciones adicionales del system prompt (broadcast context,
                instrucción [SKIP], etc.). Se pasan via ``set_extra_system_sections`` ANTES
                de invocar ``execute``.
        """
        chat_id = update.effective_chat.id
        es_grupo = chat_type in _TIPOS_GRUPO
        secciones: list[str] = list(extra_sections or [])

        # Inyectar contexto de broadcast si hay receiver y es un grupo.
        if es_grupo and self._broadcast_receiver is not None:
            rendered = self._broadcast_receiver.render(str(chat_id))
            if rendered:
                secciones.insert(0, rendered)

        # Inyectar secciones adicionales en el use case ANTES de execute().
        secciones_no_vacias = [s for s in secciones if s]
        self._container.run_agent.set_extra_system_sections(secciones_no_vacias)

        self._container.set_channel_context(
            ChannelContext(
                channel_type="telegram",
                user_id=str(update.effective_user.id),
            )
        )
        live_sink = TelegramLiveIntermediateSink(bot=self, chat_id=chat_id)
        try:
            response = await self._container.run_agent.execute(
                user_input,
                intermediate_sink=live_sink,
                channel="telegram",
                chat_id=str(chat_id),
            )

            # Verificar marcador [SKIP] — solo aplica en modo autónomo en grupos.
            # La respuesta contiene SOLO el marcador → no enviar nada ni emitir broadcast.
            if self._behavior == "autonomous" and es_grupo and response.strip() == "[SKIP]":
                logger.debug(
                    "autonomous_skip detectado (agent=%s, chat_id=%s)",
                    self._agent_cfg.id,
                    chat_id,
                )
                return

            await update.message.reply_text(format_response(response), parse_mode=ParseMode.HTML)
            await self._set_reaction(update, "✅")

            # Emitir broadcast DESPUÉS del reply, solo para grupos, fire-and-forget.
            if es_grupo and self._broadcast_emitter is not None:
                msg_broadcast = BroadcastMessage(
                    timestamp=time.time(),
                    agent_id=self._agent_cfg.id,
                    chat_id=str(chat_id),
                    message=response,
                )
                asyncio.ensure_future(self._emitir_broadcast(msg_broadcast))

        except Exception as exc:
            logger.exception("Error procesando mensaje Telegram para '%s'", self._agent_cfg.id)
            await update.message.reply_text(f"Error: {exc}")
            await self._set_reaction(update, "❌")
        finally:
            self._container.set_channel_context(None)
            # Limpiar extra_sections después del turno para no contaminar el siguiente.
            self._container.run_agent.set_extra_system_sections([])

    async def _emitir_broadcast(self, msg: BroadcastMessage) -> None:
        """Emite un BroadcastMessage al canal. Captura y loguea excepciones silenciosamente.

        Este método es invocado via ``asyncio.ensure_future`` — cualquier excepción
        aquí NO debe propagarse al caller (Telegram reply ya fue enviado).
        """
        try:
            await self._broadcast_emitter.emit(msg)  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning(
                "Fallo al emitir broadcast (agent=%s, chat_id=%s): %s",
                msg.agent_id,
                msg.chat_id,
                exc,
            )

    async def subscribe_broadcast_trigger(self) -> None:
        """Registra el callback de ingress para responder a mensajes broadcast.

        Solo aplica a agentes con ``behavior: autonomous`` y un ``broadcast_receiver``
        disponible. Para ``listen`` o ``mention`` no tiene sentido — el primero no
        responde nunca y el segundo requiere una mención Telegram real (que no
        existe en un mensaje llegado por TCP).
        """
        if self._broadcast_receiver is None:
            return
        if self._behavior != "autonomous":
            logger.debug(
                "Bot '%s': behavior=%s — no se registra trigger de broadcast",
                self._agent_cfg.id,
                self._behavior,
            )
            return
        await self._broadcast_receiver.subscribe(self._on_broadcast_received)
        logger.info(
            "Bot '%s': suscripto a broadcast como trigger (autonomous)",
            self._agent_cfg.id,
        )

    async def _on_broadcast_received(self, msg: BroadcastMessage) -> None:
        """Callback invocado por el adapter por cada ``BroadcastMessage`` válido.

        Decide si el bot debe responder. Silencioso y defensivo: cualquier
        excepción queda aquí (fire-and-forget desde el adapter).
        """
        try:
            preview = msg.message[:200].replace("\n", " ")
            logger.info(
                "broadcast.trigger.eval agent=%s from=%s chat_id=%s preview=%r",
                self._agent_cfg.id,
                msg.agent_id,
                msg.chat_id,
                preview,
            )

            jitter = random.uniform(
                BROADCAST_TRIGGER_JITTER_MIN_SEC, BROADCAST_TRIGGER_JITTER_MAX_SEC
            )
            logger.debug(
                "broadcast.trigger.jitter agent=%s chat_id=%s delay=%.2fs",
                self._agent_cfg.id,
                msg.chat_id,
                jitter,
            )
            await asyncio.sleep(jitter)

            # Rate limiter por (agent_id, chat_id) — la misma ventana que usan
            # los mensajes entrantes de Telegram en modo autonomous. Evita
            # tormentas bot-to-bot. La decisión fina de responder o [SKIP]
            # la toma el LLM.
            if self._rate_limiter is not None:
                breach = self._rate_limiter.check_and_increment(
                    self._agent_cfg.id,
                    msg.chat_id,
                    self._rate_limit_max,
                )
                if breach is not None:
                    logger.info(
                        "broadcast.trigger.skip.rate_limited agent=%s chat_id=%s counter=%d",
                        self._agent_cfg.id,
                        msg.chat_id,
                        breach.counter,
                    )
                    return

            logger.info(
                "broadcast.trigger.fire agent=%s from=%s chat_id=%s",
                self._agent_cfg.id,
                msg.agent_id,
                msg.chat_id,
            )
            await self._respond_to_broadcast(msg)
        except Exception:
            logger.exception(
                "Error procesando broadcast trigger (agent=%s, from=%s, chat_id=%s)",
                self._agent_cfg.id,
                msg.agent_id,
                msg.chat_id,
            )

    async def _respond_to_broadcast(self, msg: BroadcastMessage) -> None:
        """Ejecuta el pipeline ante un broadcast recibido y responde al grupo.

        El input al LLM lleva un prefijo ``[<agent_id> dijo en el grupo]: ...`` para
        que el modelo sepa que la fuente es otro bot. Respeta ``[SKIP]`` y vuelve a
        emitir broadcast para mantener el contexto cruzado consistente.
        """
        chat_id_int = int(msg.chat_id)
        contenido = f"[{msg.agent_id} dijo en el grupo]: {msg.message}"

        secciones: list[str] = []
        if self._broadcast_receiver is not None:
            rendered = self._broadcast_receiver.render(msg.chat_id)
            if rendered:
                secciones.append(rendered)

        seccion_skip = (
            "## Modo autónomo\n"
            "Si después de leer el contexto considerás que no tenés nada útil que aportar "
            "al grupo, respondé EXACTAMENTE con `[SKIP]` (mayúsculas, entre corchetes, nada "
            "más). El sistema detecta ese marcador y no enviará nada al grupo."
        )
        secciones.append(seccion_skip)

        self._container.run_agent.set_extra_system_sections([s for s in secciones if s])
        self._container.set_channel_context(
            ChannelContext(channel_type="telegram", user_id=msg.agent_id)
        )
        try:
            response = await self._container.run_agent.execute(
                contenido,
                channel="telegram",
                chat_id=msg.chat_id,
            )

            if response.strip() == "[SKIP]":
                logger.debug(
                    "broadcast_trigger_skip (agent=%s, chat_id=%s, from=%s)",
                    self._agent_cfg.id,
                    msg.chat_id,
                    msg.agent_id,
                )
                return

            await self._app.bot.send_message(
                chat_id=chat_id_int,
                text=format_response(response),
                parse_mode=ParseMode.HTML,
            )

            # Re-emitimos broadcast para que los otros bots vean nuestra respuesta
            # en su propio buffer (contexto para el próximo turno).
            if self._broadcast_emitter is not None:
                msg_out = BroadcastMessage(
                    timestamp=time.time(),
                    agent_id=self._agent_cfg.id,
                    chat_id=msg.chat_id,
                    message=response,
                )
                asyncio.ensure_future(self._emitir_broadcast(msg_out))
        finally:
            self._container.set_channel_context(None)
            self._container.run_agent.set_extra_system_sections([])

    async def verificar_bot_username(self) -> None:
        """Verifica que ``bot_username`` en config coincide con el username real del bot.

        Llama a ``get_me()`` UNA SOLA VEZ al arranque. No bloquea ni falla el startup:
        - Si ``bot_username`` es ``None`` en config → INFO alentando a configurarlo.
        - Si el username real difiere del configurado → WARNING (no bloquea).
        - Si ``get_me()`` falla → WARNING (no bloquea).

        Solo aplica si hay un bloque ``broadcast:`` con ``behavior != "listen"`` —
        para ``listen`` la detección de menciones no se usa, así que el username
        no importa operativamente. Sin broadcast config, este método no hace nada.
        """
        broadcast_raw = self._agent_cfg.channels.get("telegram", {})
        if hasattr(broadcast_raw, "get"):
            broadcast_block = broadcast_raw.get("broadcast")
        else:
            broadcast_block = getattr(broadcast_raw, "broadcast", None)

        if broadcast_block is None:
            # Sin bloque broadcast → nada que validar.
            return

        try:
            me = await self._app.bot.get_me()
        except Exception as exc:
            logger.warning(
                "Telegram bot '%s': no se pudo obtener bot info via get_me(): %s",
                self._agent_cfg.id,
                exc,
            )
            return

        real_username = me.username  # puede ser None si el bot no tiene username

        if self._bot_username is None:
            logger.info(
                "Telegram bot '%s': broadcast.bot_username no configurado "
                "(username real: @%s). Configuralo para que el modo 'mention' funcione correctamente.",
                self._agent_cfg.id,
                real_username or "<sin username>",
            )
            return

        if real_username is None:
            logger.warning(
                "Telegram bot '%s': get_me() devolvió username=None "
                "(config declara bot_username='%s'). Verificá el token.",
                self._agent_cfg.id,
                self._bot_username,
            )
            return

        if real_username.lower() != self._bot_username.lower():
            logger.warning(
                "Telegram bot '%s': bot_username en config ('%s') no coincide "
                "con el username real del bot ('@%s'). "
                "Actualizá broadcast.bot_username en la config para evitar fallos en mention detection.",
                self._agent_cfg.id,
                self._bot_username,
                real_username,
            )
        else:
            logger.info(
                "Telegram bot '%s': bot_username validado correctamente ('@%s')",
                self._agent_cfg.id,
                real_username,
            )

    async def setup_commands(self) -> None:
        """Registra el menú de comandos en Telegram. Reemplaza cualquier lista previa
        (incluidos comandos viejos seteados desde BotFather)."""
        commands = [
            BotCommand("start", "Presentación del agente"),
            BotCommand("help", "Lista de comandos disponibles"),
            BotCommand("clear", "Limpiar historial de conversación"),
            BotCommand("consolidate", "Extraer recuerdos del historial"),
            BotCommand("scheduler", "Gestionar tareas programadas (list/show/enable/disable)"),
            BotCommand("chatid", "Obtener el ID del chat actual (útil para configurar grupos)"),
        ]
        try:
            await self._app.bot.set_my_commands(commands)
            logger.info(
                "Telegram bot '%s': menú de comandos actualizado (%d comandos)",
                self._agent_cfg.id,
                len(commands),
            )
        except Exception as exc:
            logger.warning(
                "Telegram bot '%s': no se pudo actualizar el menú de comandos: %s",
                self._agent_cfg.id,
                exc,
            )

    async def send_message(self, chat_id: int, text: str) -> None:
        """Envía un mensaje proactivo fuera del contexto de un handler.

        Usado por `ChannelSenderAdapter` para triggers `channel_send` del
        scheduler. Delega en el bot interno de `python-telegram-bot`.
        """
        await self._app.bot.send_message(chat_id=chat_id, text=text)

    def run_polling(self) -> None:
        """Inicia el bot en modo polling (bloqueante)."""
        logger.info(
            "Telegram bot iniciado para agente '%s'",
            self._agent_cfg.id,
        )
        self._app.run_polling(drop_pending_updates=True)
