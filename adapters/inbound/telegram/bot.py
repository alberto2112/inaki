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

from adapters.inbound.telegram.message_mapper import telegram_update_to_input, format_response
from core.domain.errors import AgentNotFoundError
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

        if not self._token:
            raise ValueError(
                f"Agente '{agent_cfg.id}': channels.telegram.token no configurado"
            )

        self._app = Application.builder().token(self._token).build()
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("consolidate", self._cmd_consolidate))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

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

        if self._reactions:
            try:
                await update.message.set_reaction("👀")
            except Exception:
                pass  # Reacciones opcionales — no deben bloquear el handler

        # Inyectar contexto de canal antes de ejecutar el agente
        self._container.set_channel_context(
            ChannelContext(
                channel_type="telegram",
                user_id=str(update.effective_user.id),
            )
        )
        try:
            response = await self._container.run_agent.execute(user_input)
            await update.message.reply_text(
                format_response(response), parse_mode=ParseMode.HTML
            )
            if self._reactions:
                try:
                    await update.message.set_reaction("✅")
                except Exception:
                    pass
        except Exception as exc:
            logger.exception("Error procesando mensaje Telegram para '%s'", self._agent_cfg.id)
            await update.message.reply_text(f"Error: {exc}")
            if self._reactions:
                try:
                    await update.message.set_reaction("❌")
                except Exception:
                    pass
        finally:
            # Limpiar contexto de canal al finalizar el turno (éxito o error)
            self._container.set_channel_context(None)

    def run_polling(self) -> None:
        """Inicia el bot en modo polling (bloqueante)."""
        logger.info(
            "Telegram bot iniciado para agente '%s'",
            self._agent_cfg.id,
        )
        self._app.run_polling(drop_pending_updates=True)
