"""Comandos slash del TelegramBot (/start, /help, /scheduler, /ratelimit, ...).

Colaborador de ``TelegramBot`` con dependencias explícitas. Son el panel del
OPERADOR: admin-only (``TelegramAuth.is_allowed``), deterministas, sin LLM. Lo
único que MUTAN en el proceso es la política de rate limit (``GroupRateLimit``),
que el flujo de grupos y el ingress de broadcast leen. ``/model``, ``/effort`` y
``/temperature`` no mutan nada propio: son atajos de la tool ``config`` del agente
(``get`` sin argumento, ``set`` con él), y el ``set`` exige ``is_operator``."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from telegram import BotCommand, Message, Update
from telegram.ext import Application, ContextTypes

from inaki.channels.telegram.auth import TelegramAuth
from inaki.channels.telegram.message_mapper import split_message
from inaki.channels.telegram.ports import TelegramBotPorts, TelegramBotSettings
from inaki.channels.telegram.rate_limit import GroupRateLimit
from inaki.scheduler.domain.task import ScheduledTask
from inaki.shared.channel_context import (
    ChannelContext,
    reset_current_channel_context,
    set_current_channel_context,
)
from inaki.shared.errors import TaskNotFoundError

logger = logging.getLogger(__name__)

_EFFORT_APAGADO = frozenset({"off", "none", "null"})
"""Palabras con las que ``/effort`` desactiva el razonamiento (``reasoning_effort: null``)."""


def _valor_model(arg: str) -> str:
    return arg


def _valor_effort(arg: str) -> str | None:
    normalizado = arg.strip().lower()
    return None if normalizado in _EFFORT_APAGADO else normalizado


def _valor_temperature(arg: str) -> float:
    # Coma decimal aceptada: es lo que sale natural en un teclado en español.
    return float(arg.replace(",", "."))


class SlashCommands:
    """Handlers de comandos + registro del menú de comandos en Telegram."""

    def __init__(
        self,
        *,
        ports: TelegramBotPorts,
        settings: TelegramBotSettings,
        app: Application,
        auth: TelegramAuth,
        rate_limit: GroupRateLimit,
        reloader: Any,
    ) -> None:
        self._ports = ports
        self._settings = settings
        self._app = app
        self._auth = auth
        self._rate_limit = rate_limit
        # DaemonReloader compartido — lo inyecta el composition root. Permite que
        # /reload cierre y reabra todos los canales del daemon. Opcional: en tests
        # o arranques sueltos puede ser None y /reload responde sin efecto.
        self._reloader = reloader

    async def cmd_chatid(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        if not self._auth.is_allowed(user.id):
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

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.message
        if user is None or message is None:
            return
        if not self._auth.is_allowed(user.id):
            return
        await message.reply_text(f"Hola, soy {self._settings.name}. {self._settings.description}")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.message
        if user is None or message is None:
            return
        if not self._auth.is_allowed(user.id):
            return
        await message.reply_text(
            "/stop — Detener la tarea en curso de ESTE chat (kill-switch)\n"
            "/consolidate — Extraer recuerdos del historial\n"
            "/reconcile — Reconsiderar y consolidar recuerdos relacionados\n"
            "/clear — Limpiar historial de ESTE chat (privado o grupo)\n"
            "/new — Consolidar memoria y limpiar este chat (alias de /consolidate + /clear)\n"
            "/scheduler list — Listar tareas programadas\n"
            "/scheduler show <id> — Detalle de una tarea\n"
            "/scheduler enable <id> — Habilitar una tarea\n"
            "/scheduler disable <id> — Deshabilitar una tarea\n"
            "/scheduler run <id> — Disparar una tarea AHORA (no toca su agenda)\n"
            "/ratelimit — Mostrar/ajustar el rate limiter de grupos (autonomous) en runtime\n"
            "/model [modelo] — Ver o cambiar el modelo del LLM (en caliente, sin persistir)\n"
            "/effort [nivel|off] — Ver o cambiar el reasoning effort (en caliente, sin persistir)\n"
            "/temperature [valor] — Ver o cambiar la temperatura (en caliente, sin persistir)\n"
            "/reload — Reiniciar el daemon (cierra y vuelve a levantar todos los canales)\n"
            "/start — Presentación\n"
            "/help — Este mensaje"
        )

    async def cmd_consolidate(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.message
        if user is None or message is None:
            return
        if not self._auth.is_allowed(user.id):
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

    async def cmd_reconcile(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.message
        if user is None or message is None:
            return
        if not self._auth.is_allowed(user.id):
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

    async def cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Kill-switch: aborta MECÁNICAMENTE el turno en curso de este chat.

        A diferencia de escribir "para" (que se inyecta al turno y depende de
        que el LLM lo obedezca), esto marca el flag de cancelación del scope:
        el tool loop lo chequea en cada checkpoint y antes de cada tool, corta,
        y responde con un resumen de dónde quedó el trabajo.
        """
        user = update.effective_user
        chat = update.effective_chat
        message = update.message
        if user is None or chat is None or message is None:
            return
        if not self._auth.is_allowed(user.id):
            return
        scope = (self._settings.id, "telegram", str(chat.id))
        if await self._ports.scope_registry.request_cancel(scope):
            await message.reply_text("🛑 Parando la tarea en curso — te resumo en un momento.")
        else:
            await message.reply_text("No hay ninguna tarea corriendo en este chat.")

    async def cmd_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Borra el historial SOLO del chat actual (privado o grupo)."""
        user = update.effective_user
        chat = update.effective_chat
        message = update.message
        if user is None or chat is None or message is None:
            return
        if not self._auth.is_allowed(user.id):
            return
        chat_id = str(chat.id)
        try:
            await self._ports.history.clear_history(
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

    async def cmd_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Alias de ``/consolidate`` + ``/clear``: consolida la memoria y LUEGO limpia
        el historial de ESTE chat, para arrancar una conversación limpia sin perder
        lo aprendido.

        El orden es deliberado: consolidar EXTRAE los recuerdos del historial, así que
        hay que hacerlo ANTES de borrarlo. Si la consolidación falla, se ABORTA el clear
        (no se toca el historial) para no perder datos silenciosamente.
        """
        user = update.effective_user
        chat = update.effective_chat
        message = update.message
        if user is None or chat is None or message is None:
            return
        if not self._auth.is_allowed(user.id):
            return
        chat_id = str(chat.id)

        # Fase 1 — consolidar: extrae recuerdos del historial antes de borrarlo.
        uc = self._ports.consolidate_memory
        if uc is not None:
            await message.reply_text("Consolidando memoria...")
            try:
                result = await uc.execute()
                await message.reply_text(result)
            except Exception as exc:
                logger.exception("Error consolidando en /new para '%s'", self._settings.id)
                await message.reply_text(
                    f"Error consolidando: {exc}\nNo limpio el historial para no perder datos."
                )
                return

        # Fase 2 — limpiar el historial del chat actual (solo si la fase 1 no falló).
        try:
            await self._ports.history.clear_history(
                channel="telegram",
                chat_id=chat_id,
            )
            await message.reply_text("Historial de este chat limpiado. Arrancamos de cero.")
        except Exception as exc:
            logger.exception(
                "Error limpiando en /new para '%s' (chat_id=%s)",
                self._settings.id,
                chat_id,
            )
            await message.reply_text(f"Error limpiando: {exc}")

    async def cmd_reload(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Reinicia el daemon: cierra todos los channels, recarga config y vuelve a levantar.

        Equivalente a ``inaki reload`` o ``POST /admin/reload``. El bot que recibió el
        comando se va a apagar como parte del reload.

        Es el reload MANUAL: descarta los overrides de runtime (``config set``) y el
        reply dice cuáles. Pide el reload ANTES de responder porque necesita la lista
        de descartados; la entrega la garantiza el drenaje del runner (``IChannel.stop``
        espera a los handlers en vuelo antes de cerrar el bot). Extiende un slash
        existente: el panel del operador, determinista y sin LLM.
        """
        user = update.effective_user
        chat = update.effective_chat
        message = update.message
        if user is None or chat is None or message is None:
            return
        if not self._auth.is_allowed(user.id):
            return
        if self._reloader is None:
            await message.reply_text(
                "Reload no disponible — el bot no fue arrancado con DaemonReloader inyectado."
            )
            return
        descartados: list[str] = self._reloader.request_reload() or []
        logger.info(
            "Reload solicitado vía /reload Telegram",
            extra={
                "agent_id": self._settings.id,
                "user_id": user.id,
                "chat_id": chat.id,
                "overrides_descartados": len(descartados),
            },
        )
        texto = "Reiniciando daemon..."
        if descartados:
            texto += "\nSe descartan los cambios de runtime:\n" + "\n".join(
                f"- {linea}" for linea in descartados
            )
        await message.reply_text(texto)

    async def cmd_model(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """`/model [modelo]` — muestra o cambia ``llm.model``."""
        await self._config_slash(update, context, "/model", "llm.model", _valor_model)

    async def cmd_effort(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """`/effort [nivel|off]` — muestra o cambia ``llm.reasoning_effort`` (``off`` = null)."""
        await self._config_slash(update, context, "/effort", "llm.reasoning_effort", _valor_effort)

    async def cmd_temperature(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """`/temperature [valor]` — muestra o cambia ``llm.temperature``."""
        await self._config_slash(
            update, context, "/temperature", "llm.temperature", _valor_temperature
        )

    async def _config_slash(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        comando: str,
        path: str,
        parsear: Callable[[str], Any],
    ) -> None:
        """Atajo de la tool ``config``: sin argumento ``get``, con uno ``set``.

        No reimplementa nada: la validación, la denylist, el no-op, el store en memoria
        y el reload que aplica el cambio son los de ``config set`` (efímero: se pierde
        con ``/reload`` o reinicio). Leer lo puede cualquiera que use los slash; cambiar,
        solo el operador (``is_operator``: privado y en la lista de operadores). El
        kernel hace cumplir ``requires_operator`` en el tool loop, pero esto no es un
        turno: el canal decide ``es_operador`` y acá se aplica, igual que en un turno.

        El ``set`` corre con el ``ChannelContext`` de este chat publicado para que la
        tool agende el aviso de "volví con el cambio aplicado" tras el reinicio.
        """
        user = update.effective_user
        chat = update.effective_chat
        message = update.message
        if user is None or chat is None or message is None:
            return
        if not self._auth.is_allowed(user.id):
            return
        tools = self._ports.tools
        if tools is None:
            await message.reply_text("La tool config no está disponible en este proceso.")
            return

        args = context.args or []
        if not args:
            resultado = await tools.execute("config", operation="get", path=path)
            await message.reply_text(resultado.output)
            return

        if not self._auth.is_operator(user.id, chat.type):
            await message.reply_text(
                f"Cambiar la config es solo para el operador, por privado. "
                f"Sin argumento, {comando} muestra el valor actual."
            )
            return
        if len(args) > 1:
            await message.reply_text(f"Uso: {comando} <valor> (un solo argumento).")
            return
        try:
            valor = parsear(args[0])
        except ValueError:
            await message.reply_text(f"Valor inválido para {comando}: '{args[0]}'.")
            return

        contexto = ChannelContext(
            channel_type="telegram", user_id=str(user.id), chat_id=str(chat.id)
        )
        token = set_current_channel_context(contexto)
        try:
            resultado = await tools.execute("config", operation="set", path=path, value=valor)
        finally:
            reset_current_channel_context(token)
        logger.info(
            "%s vía Telegram",
            comando,
            extra={
                "agent_id": self._settings.id,
                "user_id": user.id,
                "path": path,
                "success": resultado.success,
            },
        )
        await message.reply_text(resultado.output)

    async def cmd_ratelimit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        if not self._auth.is_allowed(user.id):
            return

        if not self._rate_limit.enabled:
            await message.reply_text(
                "El rate limiter solo aplica en grupos con behavior=autonomous — "
                "este agente no tiene ese modo configurado."
            )
            return

        args = context.args or []

        # Sin argumentos: mostrar estado actual.
        if not args:
            display_window = self._rate_limit.window_seconds
            await message.reply_text(
                f"Rate limiter actual (intervenciones consecutivas sin humano):\n"
                f"  count = {self._rate_limit.max_count} respuestas seguidas "
                f"(default: {self._rate_limit.default_max_count})\n"
                f"  window = {display_window}s de cooldown al llegar al count "
                f"(default: {self._rate_limit.default_window_seconds}s)\n"
                f"\nUn mensaje humano re-arma el contador al instante.\n"
                f"\n"
                f"Sintaxis:\n"
                f"  /ratelimit <count>\n"
                f"  /ratelimit <count> <window>\n"
                f"  /ratelimit reset"
            )
            return

        # Reset → volver a los valores de config.
        if args[0].lower() == "reset":
            self._rate_limit.restore_defaults()
            await message.reply_text(
                f"Rate limiter reseteado a config: "
                f"count={self._rate_limit.max_count}, "
                f"window={self._rate_limit.default_window_seconds}s."
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

        self._rate_limit.set(count, window)

        # Construir respuesta con avisos de clamp si aplican.
        current_window = self._rate_limit.window_seconds
        partes = [f"Rate limiter actualizado: count={count}, window={current_window}s."]
        if count_clamped:
            partes.append(f"⚠ count clampeado de {count_raw} a 99 (máx).")
        if window_clamped:
            partes.append(f"⚠ window clampeada de {window_raw}s a 900s (máx).")
        partes.append("(en memoria — se pierde al reiniciar el daemon)")
        await message.reply_text("\n".join(partes))

    async def cmd_scheduler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """`/scheduler {list|show|enable|disable|run} [id]` — gestión de tareas.

        ``run`` dispara la tarea AHORA sin tocar su agenda (mismo motor que
        ``inaki scheduler run <id>``: ``IManualTaskRunner.run_task_now``).
        """
        user = update.effective_user
        message = update.message
        if user is None or message is None:
            return
        if not self._auth.is_allowed(user.id):
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
                "/scheduler disable <id>\n"
                "/scheduler run <id>"
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

        if sub in {"show", "enable", "disable", "run"}:
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
                elif sub == "disable":
                    await uc.disable_task(task_id)
                    await message.reply_text(f"Tarea {task_id} deshabilitada.")
                else:  # run
                    await self._scheduler_run(message, task_id)
            except TaskNotFoundError:
                await message.reply_text(f"Tarea {task_id} no encontrada.")
            except Exception as exc:
                logger.exception(
                    "Error en /scheduler %s %s para '%s'", sub, task_id, self._settings.id
                )
                await message.reply_text(f"Error: {exc}")
            return

        await message.reply_text(
            f"Sub-comando desconocido: {sub}. Usá list, show, enable, disable o run."
        )

    async def _scheduler_run(self, message: Message, task_id: int) -> None:
        """Dispara la tarea ``task_id`` on-demand y reporta el resultado.

        Espeja ``inaki scheduler run <id>``: NO destructivo (no toca status /
        next_run / executions_remaining) y queda en ``task_logs`` marcado como
        ``trigger=manual``. Las excepciones (incluida ``TaskNotFoundError``)
        suben al caller, que ya las mapea a mensajes de usuario.
        """
        runner = self._ports.manual_task_runner
        if runner is None:
            await message.reply_text("El disparo manual no está disponible en este proceso.")
            return

        await message.reply_text(f"Disparando tarea {task_id}...")
        result = await runner.run_task_now(task_id)

        if not result.success:
            await message.reply_text(f"Tarea {task_id} falló: {result.error}")
            return

        await message.reply_text(f"Tarea {task_id} ejecutada — agenda intacta.")
        # El output de un agent_send es la respuesta cruda del LLM: sin trocear,
        # Telegram rechaza con BadRequest cualquier texto de más de 4096 chars y
        # el usuario vería un "Error:" pese a que la tarea corrió bien.
        if result.output:
            for chunk in split_message(result.output):
                await message.reply_text(chunk)

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
            BotCommand("stop", "Detener la tarea en curso de este chat (kill-switch)"),
            BotCommand("clear", "Limpiar historial de este chat"),
            BotCommand("new", "Consolidar memoria y empezar de cero en este chat"),
            BotCommand("consolidate", "Extraer recuerdos del historial"),
            BotCommand("reconcile", "Reconsiderar recuerdos relacionados"),
            BotCommand("scheduler", "Gestionar tareas programadas (list/show/enable/disable/run)"),
            BotCommand("chatid", "Obtener el ID del chat actual (útil para configurar grupos)"),
            BotCommand(
                "ratelimit", "Ver/ajustar el rate limiter de grupos (autonomous) en runtime"
            ),
            BotCommand("model", "Ver o cambiar el modelo del LLM (en caliente)"),
            BotCommand("effort", "Ver o cambiar el reasoning effort (en caliente)"),
            BotCommand("temperature", "Ver o cambiar la temperatura del LLM (en caliente)"),
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
