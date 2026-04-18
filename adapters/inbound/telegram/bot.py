"""
TelegramBot — adaptador inbound para Telegram.

Un bot por agente. Se levanta solo si el agente tiene channels.telegram.token en su config.
Valida que el user_id esté en allowed_user_ids (si la lista no está vacía).
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from adapters.inbound.telegram.message_mapper import (
    extract_audio_payload,
    format_response,
    telegram_update_to_input,
)
from adapters.outbound.intermediate_sinks.telegram_live import TelegramLiveIntermediateSink
from core.domain.errors import TranscriptionError
from core.domain.value_objects.channel_context import ChannelContext
from infrastructure.config import AgentConfig
from infrastructure.container import AgentContainer

logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self, agent_cfg: AgentConfig, container: AgentContainer) -> None:
        self._agent_cfg = agent_cfg
        self._container = container
        tg_cfg = agent_cfg.channels.get("telegram", {})
        self._token: str = tg_cfg.get("token", "")
        self._allowed_ids: list[str] = [str(uid) for uid in tg_cfg.get("allowed_user_ids", [])]
        self._reactions: bool = tg_cfg.get("reactions", False)
        self._voice_enabled: bool = tg_cfg.get("voice_enabled", True)

        if not self._token:
            raise ValueError(f"Agente '{agent_cfg.id}': channels.telegram.token no configurado")

        self._app = Application.builder().token(self._token).build()
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("consolidate", self._cmd_consolidate))
        self._app.add_handler(CommandHandler("clear", self._cmd_clear))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        # Handlers de voz ANTES del de texto (el dispatcher de python-telegram-bot
        # evalúa handlers en orden de registro). Sólo se registran si el feature
        # flag está activo; con voice_enabled=False no se engancha ningún filtro.
        if self._voice_enabled:
            self._app.add_handler(MessageHandler(filters.VOICE, self._handle_voice_message))
            self._app.add_handler(MessageHandler(filters.AUDIO, self._handle_voice_message))
            self._app.add_handler(
                MessageHandler(filters.VIDEO_NOTE, self._handle_voice_message)
            )
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

    def _is_allowed(self, user_id: int) -> bool:
        if not self._allowed_ids:
            return True  # Lista vacía = todos permitidos
        return str(user_id) in self._allowed_ids

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

        await self._set_reaction(update, "👀")
        await self._run_pipeline(update, user_input)

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
            logger.warning(
                "Transcripción fallida para agente '%s': %s", self._agent_cfg.id, exc
            )
            await update.message.reply_text(f"No pude transcribir el audio: {exc}")
            await self._set_reaction(update, "❌")
            return

        if not transcribed or not transcribed.strip():
            await update.message.reply_text("La transcripción vino vacía.")
            await self._set_reaction(update, "❌")
            return

        await self._run_pipeline(update, transcribed)

    async def _set_reaction(self, update: Update, emoji: str) -> None:
        """Envía una reacción al mensaje si `reactions` está activo. Silencia fallos."""
        if not self._reactions:
            return
        try:
            await update.message.set_reaction(emoji)
        except Exception:
            pass  # Reacciones opcionales — no deben bloquear el handler.

    async def _run_pipeline(self, update: Update, user_input: str) -> None:
        """Ejecuta el agente con `user_input` (ya sea texto tipeado o transcripto).

        Centraliza el ciclo común: channel_context → live_sink → run_agent.execute
        → reply HTML → reacción ✅/❌ → limpiar contexto al final.
        """
        self._container.set_channel_context(
            ChannelContext(
                channel_type="telegram",
                user_id=str(update.effective_user.id),
            )
        )
        live_sink = TelegramLiveIntermediateSink(
            bot=self, chat_id=update.effective_chat.id
        )
        try:
            response = await self._container.run_agent.execute(
                user_input, intermediate_sink=live_sink
            )
            await update.message.reply_text(format_response(response), parse_mode=ParseMode.HTML)
            await self._set_reaction(update, "✅")
        except Exception as exc:
            logger.exception("Error procesando mensaje Telegram para '%s'", self._agent_cfg.id)
            await update.message.reply_text(f"Error: {exc}")
            await self._set_reaction(update, "❌")
        finally:
            self._container.set_channel_context(None)

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
