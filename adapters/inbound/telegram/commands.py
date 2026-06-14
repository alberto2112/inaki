"""Comandos slash del TelegramBot (/start, /help, /scheduler, /ratelimit, ...).

Mixin de ``TelegramBot`` — los métodos corren con ``self`` del bot. Estado que
MUTA acá y se lee en otros módulos: ``_rate_limit_max`` (lo consume el rate
limiting de group_flow/broadcast)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from telegram import BotCommand, Update
from telegram.ext import ContextTypes

from core.domain.entities.task import ScheduledTask
from core.domain.errors import TaskNotFoundError


if TYPE_CHECKING:
    from collections.abc import Callable

    from adapters.inbound.telegram.ports import TelegramBotPorts, TelegramBotSettings
    from telegram.ext import Application

logger = logging.getLogger(__name__)


class TelegramCommandsMixin:
    """Handlers de comandos + registro del menú de comandos en Telegram."""

    # Contrato con TelegramBot — estado y colaboradores que este mixin consume.
    _settings: TelegramBotSettings
    _ports: TelegramBotPorts
    _app: Application
    _reloader: Any
    _rate_limiter: Any
    _rate_limit_max: int
    _rate_limit_max_default: int
    _rate_limit_window_default: int
    _is_allowed: Callable[[int], bool]

    async def _cmd_chatid(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/chatid — responde con el ID del chat actual.

        Bypasea ``allowed_chat_ids`` para poder usarlo antes de agregar el grupo a la whitelist
        (bootstrap de configuración). Sin embargo, sigue respetando ``allowed_user_ids``:
        si el usuario no está autorizado, se ignora silenciosamente.

        Útil para obtener el ``chat_id`` de un grupo y agregarlo a ``allowed_chat_ids``.
        """
        user = update.effective_user
        chat = update.effective_chat
        message = update.message
        if user is None or chat is None or message is None:
            return
        if not self._is_allowed(user.id):
            return

        chat_id = chat.id
        chat_type = chat.type
        logger.info(
            "/chatid invocado",
            extra={
                "user_id": user.id,
                "chat_id": chat_id,
                "chat_type": chat_type,
            },
        )
        await message.reply_text(str(chat_id))

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.message
        if user is None or message is None:
            return
        if not self._is_allowed(user.id):
            return
        await message.reply_text(f"Hola, soy {self._settings.name}. {self._settings.description}")

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.message
        if user is None or message is None:
            return
        if not self._is_allowed(user.id):
            return
        await message.reply_text(
            "/consolidate — Extraer recuerdos del historial\n"
            "/reconcile — Reconsiderar y consolidar recuerdos relacionados\n"
            "/clear — Limpiar historial de ESTE chat (privado o grupo)\n"
            "/clear_all — Limpiar TODO el historial del agente (todos los chats)\n"
            "/scheduler list — Listar tareas programadas\n"
            "/scheduler show <id> — Detalle de una tarea\n"
            "/scheduler enable <id> — Habilitar una tarea\n"
            "/scheduler disable <id> — Deshabilitar una tarea\n"
            "/ratelimit — Mostrar/ajustar el rate limiter del broadcast en runtime\n"
            "/reload — Reiniciar el daemon (cierra y vuelve a levantar todos los canales)\n"
            "/start — Presentación\n"
            "/help — Este mensaje"
        )

    async def _cmd_consolidate(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.message
        if user is None or message is None:
            return
        if not self._is_allowed(user.id):
            return
        uc = self._ports.consolidate_memory
        if uc is None:
            await message.reply_text("La consolidación de memoria no está disponible.")
            return
        await message.reply_text("Consolidando memoria...")
        try:
            result = await uc.execute()
            await message.reply_text(result)
        except Exception as exc:
            await message.reply_text(f"Error: {exc}")

    async def _cmd_reconcile(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.message
        if user is None or message is None:
            return
        if not self._is_allowed(user.id):
            return
        uc = self._ports.reconcile_memory
        if uc is None:
            await message.reply_text("La reconciliación de memoria no está disponible.")
            return
        await message.reply_text("Reconciliando memoria...")
        try:
            result = await uc.execute()
            await message.reply_text(result)
        except Exception as exc:
            await message.reply_text(f"Error: {exc}")

    async def _cmd_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Borra el historial SOLO del chat actual (privado o grupo).

        Para limpiar el historial del agente en todos los chats, usar /clear_all.
        """
        user = update.effective_user
        chat = update.effective_chat
        message = update.message
        if user is None or chat is None or message is None:
            return
        if not self._is_allowed(user.id):
            return
        chat_id = str(chat.id)
        try:
            await self._ports.run_agent.clear_history(
                channel="telegram",
                chat_id=chat_id,
            )
            await message.reply_text("Historial de este chat limpiado.")
        except Exception as exc:
            logger.exception(
                "Error en /clear Telegram para '%s' (chat_id=%s)",
                self._settings.id,
                chat_id,
            )
            await message.reply_text(f"Error: {exc}")

    async def _cmd_clear_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Borra TODO el historial del agente (todos los canales y chats).

        También resetea el ``agent_state`` (sticky skills/tools).
        """
        user = update.effective_user
        message = update.message
        if user is None or message is None:
            return
        if not self._is_allowed(user.id):
            return
        try:
            await self._ports.run_agent.clear_history()
            await message.reply_text("Historial completo del agente limpiado.")
        except Exception as exc:
            logger.exception("Error en /clear_all Telegram para '%s'", self._settings.id)
            await message.reply_text(f"Error: {exc}")

    async def _cmd_reload(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Reinicia el daemon: cierra todos los channels, recarga config y vuelve a levantar.

        Equivalente a ``inaki reload`` o ``POST /admin/reload``. El bot que recibió el
        comando se va a apagar como parte del reload — el reply se envía ANTES de señalar
        al runner para que el usuario tenga feedback antes del corte.
        """
        user = update.effective_user
        chat = update.effective_chat
        message = update.message
        if user is None or chat is None or message is None:
            return
        if not self._is_allowed(user.id):
            return
        if self._reloader is None:
            await message.reply_text(
                "Reload no disponible — el bot no fue arrancado con DaemonReloader inyectado."
            )
            return
        await message.reply_text("Reiniciando daemon...")
        logger.info(
            "Reload solicitado vía /reload Telegram",
            extra={
                "agent_id": self._settings.id,
                "user_id": user.id,
                "chat_id": chat.id,
            },
        )
        self._reloader.request_reload()

    async def _cmd_ratelimit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """`/ratelimit [count [window] | reset]` — override en runtime del rate limiter.

        Sintaxis:
        - ``/ratelimit`` → muestra los valores actuales (count y ventana en segundos).
        - ``/ratelimit <count>`` → cambia solo el count. Clamp: 1..99.
        - ``/ratelimit <count> <window>`` → cambia ambos. Clamp: count 1..99, window 1..900s.
        - ``/ratelimit reset`` → vuelve a los valores de config.

        El cambio es solo en memoria — al reiniciar el daemon se reaplican los valores
        de ``~/.inaki/config/...``. Aplica al bot completo (todos los chats).
        """
        user = update.effective_user
        message = update.message
        if user is None or message is None:
            return
        if not self._is_allowed(user.id):
            return

        if self._rate_limiter is None:
            await message.reply_text(
                "El broadcast no está configurado en este agente — el rate limiter no aplica."
            )
            return

        args = context.args or []

        # Sin argumentos: mostrar estado actual.
        if not args:
            display_window = int(self._rate_limiter.window_seconds)
            await message.reply_text(
                f"Rate limiter actual:\n"
                f"  count = {self._rate_limit_max} (default: {self._rate_limit_max_default})\n"
                f"  window = {display_window}s (default: {self._rate_limit_window_default}s)\n"
                f"\n"
                f"Sintaxis:\n"
                f"  /ratelimit <count>\n"
                f"  /ratelimit <count> <window>\n"
                f"  /ratelimit reset"
            )
            return

        # Reset → volver a los valores de config.
        if args[0].lower() == "reset":
            self._rate_limit_max = self._rate_limit_max_default
            self._rate_limiter.set_window(float(self._rate_limit_window_default))
            logger.info(
                "ratelimit.reset agent=%s count=%d window=%ds",
                self._settings.id,
                self._rate_limit_max,
                self._rate_limit_window_default,
            )
            await message.reply_text(
                f"Rate limiter reseteado a config: "
                f"count={self._rate_limit_max}, window={self._rate_limit_window_default}s."
            )
            return

        # Parseo de count.
        try:
            count_raw = int(args[0])
        except ValueError:
            await message.reply_text(
                f"Count inválido: '{args[0]}'. Debe ser un entero entre 1 y 99."
            )
            return

        if count_raw < 1:
            await message.reply_text("Count debe ser >= 1.")
            return

        # Clamp count a [1, 99].
        count = min(count_raw, 99)
        count_clamped = count_raw > 99

        # Parseo opcional de window.
        window: int | None = None
        window_clamped = False
        if len(args) >= 2:
            try:
                window_raw = int(args[1])
            except ValueError:
                await message.reply_text(
                    f"Window inválida: '{args[1]}'. Debe ser un entero entre 1 y 900 (segundos)."
                )
                return
            if window_raw < 1:
                await message.reply_text("Window debe ser >= 1 segundo.")
                return
            window = min(window_raw, 900)
            window_clamped = window_raw > 900

        # Aplicar mutaciones.
        self._rate_limit_max = count
        if window is not None:
            self._rate_limiter.set_window(float(window))

        # Construir respuesta con avisos de clamp si aplican.
        current_window = int(self._rate_limiter.window_seconds)
        partes = [f"Rate limiter actualizado: count={count}, window={current_window}s."]
        if count_clamped:
            partes.append(f"⚠ count clampeado de {count_raw} a 99 (máx).")
        if window_clamped:
            partes.append(f"⚠ window clampeada de {window_raw}s a 900s (máx).")
        partes.append("(en memoria — se pierde al reiniciar el daemon)")

        logger.info(
            "ratelimit.update agent=%s count=%d window=%ds",
            self._settings.id,
            count,
            current_window,
        )
        await message.reply_text("\n".join(partes))

    async def _cmd_scheduler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """`/scheduler {list|show|enable|disable} [id]` — gestión read-only/toggle de tareas."""
        user = update.effective_user
        message = update.message
        if user is None or message is None:
            return
        if not self._is_allowed(user.id):
            return

        uc = self._ports.schedule_task
        if uc is None:
            await message.reply_text("El scheduler no está inicializado en este proceso.")
            return

        args = context.args or []
        if not args:
            await message.reply_text(
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
                logger.exception("Error en /scheduler list para '%s'", self._settings.id)
                await message.reply_text(f"Error: {exc}")
                return
            if not tasks:
                await message.reply_text("No hay tareas programadas.")
                return
            await message.reply_text(self._format_task_list(tasks))
            return

        if sub in {"show", "enable", "disable"}:
            if len(args) < 2:
                await message.reply_text(f"Uso: /scheduler {sub} <id>")
                return
            try:
                task_id = int(args[1])
            except ValueError:
                await message.reply_text(f"ID inválido: {args[1]}")
                return

            try:
                if sub == "show":
                    task = await uc.get_task(task_id)
                    await message.reply_text(self._format_task_detail(task))
                elif sub == "enable":
                    await uc.enable_task(task_id)
                    await message.reply_text(f"Tarea {task_id} habilitada.")
                else:  # disable
                    await uc.disable_task(task_id)
                    await message.reply_text(f"Tarea {task_id} deshabilitada.")
            except TaskNotFoundError:
                await message.reply_text(f"Tarea {task_id} no encontrada.")
            except Exception as exc:
                logger.exception(
                    "Error en /scheduler %s %s para '%s'", sub, task_id, self._settings.id
                )
                await message.reply_text(f"Error: {exc}")
            return

        await message.reply_text(
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

    async def setup_commands(self) -> None:
        """Registra el menú de comandos en Telegram. Reemplaza cualquier lista previa
        (incluidos comandos viejos seteados desde BotFather)."""
        commands = [
            BotCommand("start", "Presentación del agente"),
            BotCommand("help", "Lista de comandos disponibles"),
            BotCommand("clear", "Limpiar historial de este chat"),
            BotCommand("clear_all", "Limpiar todo el historial del agente"),
            BotCommand("consolidate", "Extraer recuerdos del historial"),
            BotCommand("reconcile", "Reconsiderar recuerdos relacionados"),
            BotCommand("scheduler", "Gestionar tareas programadas (list/show/enable/disable)"),
            BotCommand("chatid", "Obtener el ID del chat actual (útil para configurar grupos)"),
            BotCommand("ratelimit", "Ver/ajustar el rate limiter del broadcast en runtime"),
            BotCommand(
                "reload", "Reiniciar el daemon (cierra y vuelve a levantar todos los canales)"
            ),
        ]
        try:
            await self._app.bot.set_my_commands(commands)
            logger.info(
                "Telegram bot '%s': menú de comandos actualizado (%d comandos)",
                self._settings.id,
                len(commands),
            )
        except Exception as exc:
            logger.warning(
                "Telegram bot '%s': no se pudo actualizar el menú de comandos: %s",
                self._settings.id,
                exc,
            )
