"""Flujo de grupos del TelegramBot: routing por behavior, buffer-delay-coalesce y flush.

Mixin de ``TelegramBot``. Estado que posee este flujo (inicializado en
``TelegramBot.__init__``): ``_pending_tasks`` (un flush task por chat) y
``_last_group_sender`` (último emisor humano por chat)."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING, Any

from telegram import Update

from adapters.inbound.telegram.message_mapper import (
    _safe_optional_str,
    compose_sender_identity,
    dirigido_a,
    format_group_message,
    hay_destinatario_explicito,
    send_html_or_plain,
)
from core.domain.skip_marker import SKIP_MARKER, is_skip_response
from core.domain.value_objects.channel_context import ChannelContext


if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from adapters.inbound.telegram.ports import TelegramBotPorts, TelegramBotSettings
    from telegram.ext import Application

    from core.ports.outbound.broadcast_port import BroadcastReceiver

logger = logging.getLogger(__name__)


class TelegramGroupFlowMixin:
    """Routing de mensajes grupales + flush con delay aleatorio."""

    # Contrato con TelegramBot — estado y colaboradores que este mixin consume.
    _settings: TelegramBotSettings
    _ports: TelegramBotPorts
    _app: Application
    _behavior: str
    _bot_username: str | None
    _broadcast_receiver: BroadcastReceiver | None
    _rate_limiter: Any
    _rate_limit_max: int
    _pending_tasks: dict[str, asyncio.Task]
    _last_group_sender: dict[str, dict[str, str | None]]
    _group_min_delay: float
    _group_max_delay: float
    _set_group_reaction: Callable[..., Coroutine[Any, Any, None]]
    _emit_event: Callable[..., Coroutine[Any, Any, None]]

    async def _handle_group_message(self, update: Update, user_input: str, chat_type: str) -> None:
        """Maneja mensajes de chats grupales según el behavior configurado.

        Flujo:
        1. Filtros: allowed_chat_ids, behavior, destinatario explícito, mention check.
        2. Persistir el mensaje en el historial via ``record_user_message``.
        3. Reaccionar 👀 (confirma al usuario que lo leíste).
        4. Rate limiter (solo en autonomous): si el sender es humano, resetea el
           limitador primero. Si hay breach, se sale sin programar respuesta pero
           el mensaje ya quedó guardado y reaccionado.
        5. Programar un flush task si no hay uno corriendo. Mensajes que lleguen
           dentro de la ventana de delay se acumulan en el historial y se procesan
           todos juntos en un único turno cuando el delay vence.
        """
        chat = update.effective_chat
        if chat is None:
            return
        chat_id = chat.id
        chat_id_str = str(chat_id)

        # La autorización del grupo (allowed_chat_ids) ya se resolvió upstream en
        # ``_is_authorized`` — todos los handlers de mensaje la chequean antes de
        # llegar acá. No se repite el check.
        behavior = self._behavior

        if behavior == "listen":
            return

        # Filtro unificado de destinatario explícito. Reply a un bot ≡ mención
        # implícita. Si el mensaje apunta a alguien concreto y ese alguien NO
        # soy yo → ignorar. Los broadcasts no pasan por aquí.
        if (
            self._bot_username
            and hay_destinatario_explicito(update.message)
            and not dirigido_a(update.message, self._bot_username)
        ):
            return

        if behavior == "mention":
            if not self._bot_username:
                logger.warning(
                    "behavior='mention' pero bot_username no configurado (agent=%s) — ignorando",
                    self._settings.id,
                )
                return
            if not dirigido_a(update.message, self._bot_username):
                return

        contenido_grupo = format_group_message(update.message)
        await self._ports.run_agent.record_user_message(
            contenido_grupo,
            channel="telegram",
            chat_id=chat_id_str,
        )
        # Snapshot del último emisor humano del chat: lo lee ``_run_group_pipeline``
        # al flushear para resolver ``{{CHANNEL.SENDER}}/USERNAME/FIRST_NAME/LAST_NAME}}``.
        # Se hace SIEMPRE (mention o autonomous): la heurística es "quien acaba de
        # hablar" sin distinguir behavior. Bots (broadcasts vía egress, no por acá)
        # no actualizan este dict — solo humanos que escriben en el grupo.
        from_user = getattr(update.message, "from_user", None)
        if from_user is not None and not getattr(from_user, "is_bot", False):
            self._last_group_sender[chat_id_str] = {
                "sender_name": _safe_optional_str(compose_sender_identity(update.message)),
                "username": _safe_optional_str(getattr(from_user, "username", None)),
                "first_name": _safe_optional_str(getattr(from_user, "first_name", None)),
                "last_name": _safe_optional_str(getattr(from_user, "last_name", None)),
            }
        await self._set_group_reaction(update, "👀")

        if behavior == "autonomous" and self._rate_limiter is not None:
            sender = update.message.from_user if update.message is not None else None
            if sender and not sender.is_bot:
                self._rate_limiter.reset(self._settings.id, chat_id_str)

            breach = self._rate_limiter.check_and_increment(
                self._settings.id,
                chat_id_str,
                self._rate_limit_max,
            )
            if breach is not None:
                logger.debug(
                    "Rate limit alcanzado en grupo (agent=%s, chat_id=%s, counter=%d)",
                    self._settings.id,
                    chat_id,
                    breach.counter,
                )
                return

        self._schedule_group_flush(chat_id_str, chat_type)

    def _schedule_group_flush(self, chat_id_str: str, chat_type: str) -> None:
        """Crea un task de flush si no hay uno activo para este chat.

        Si ya hay uno corriendo, el mensaje recién persistido será visto por ese
        task cuando despierte — no creamos uno nuevo. Idempotente.
        """
        task = self._pending_tasks.get(chat_id_str)
        if task is None or task.done():
            self._pending_tasks[chat_id_str] = asyncio.create_task(
                self._flush_group_buffer(chat_id_str, chat_type)
            )

    async def _flush_group_buffer(self, chat_id_str: str, chat_type: str) -> None:
        """Espera el delay aleatorio y dispara el pipeline para este chat.

        El pipeline lee el historial vía ``execute()`` sin user_input — la query
        del turno se deriva del trailing batch de role=user del historial.
        """
        delay = random.uniform(self._group_min_delay, self._group_max_delay)
        logger.debug(
            "group_response_delay agent=%s chat_id=%s delay=%.2fs",
            self._settings.id,
            chat_id_str,
            delay,
        )
        await asyncio.sleep(delay)
        await self._run_group_pipeline(chat_id_str, chat_type)

    async def _run_group_pipeline(self, chat_id_str: str, chat_type: str) -> None:
        """Pipeline de flush para grupos.

        A diferencia de ``_run_pipeline`` (privados/voice), este NO recibe ``Update``:
        construye la respuesta a partir del historial vía ``execute()`` sin
        ``user_input`` y la envía al chat con ``send_message`` (no ``reply_text``).

        Inyecta contexto de broadcast via ``broadcast_receiver.render`` y, en
        modo autónomo, la sección ``__SKIP__`` que permite al LLM optar por silencio.
        """
        chat_id_int = int(chat_id_str)
        secciones: list[str] = []

        if self._broadcast_receiver is not None:
            rendered = self._broadcast_receiver.render(chat_id_str)
            if rendered:
                secciones.append(rendered)

        if self._behavior == "autonomous":
            secciones.append(
                "## Modo autónomo\n"
                "Si después de leer el contexto considerás que no tenés nada útil que aportar "
                "al grupo, respondé EXACTAMENTE con `__SKIP__` (mayúsculas, doble guion bajo "
                "antes y después, sin llamar ninguna tool, nada más). El sistema detecta ese "
                "marcador y no enviará nada al grupo."
            )

        self._ports.run_agent.set_extra_system_sections([s for s in secciones if s])
        # Heurística "último emisor del batch": ``_handle_group_message`` snapshotea
        # el ``from_user`` de cada mensaje humano que entra en el chat, sobrescribiendo
        # el slot por chat_id. Al flushear, este dict refleja "la última persona que
        # habló en este chat" — la elegimos como sender canónica del turno. Si el
        # buffer trae varios autores, gana el más reciente. Si el dict no tiene
        # entrada para este chat (primer flush sin mensajes humanos previos, ej.
        # disparo por broadcast), los 4 campos quedan en ``None`` y las variables
        # ``{{CHANNEL.SENDER}}/USERNAME/FIRST_NAME/LAST_NAME}}`` se dejan literales.
        last_sender = self._last_group_sender.get(chat_id_str, {})
        turn_ctx = ChannelContext(
            channel_type="telegram",
            user_id=self._settings.id,
            chat_id=chat_id_str,
            is_group=True,
            sender_name=last_sender.get("sender_name"),
            username=last_sender.get("username"),
            first_name=last_sender.get("first_name"),
            last_name=last_sender.get("last_name"),
        )
        try:
            # Scope (channel, chat_id) derivado de turn_ctx dentro de execute.
            response = await self._ports.run_agent.execute(
                ctx=turn_ctx,
                skip_marker=SKIP_MARKER if self._behavior == "autonomous" else None,
            )

            if not response:
                # execute() devolvió vacío — historial sin trailing role=user.
                # Puede pasar si otro flush concurrente ya consumió el batch.
                return

            # Marcador __SKIP__ — solo aplica en modo autónomo. Detección
            # TOLERANTE (mismo criterio que `_run_pipeline`): aceptamos la
            # ocurrencia en cualquier parte de la respuesta. La persistencia
            # ya se descartó arriba vía skip_marker con la misma regla.
            if self._behavior == "autonomous" and is_skip_response(response):
                logger.debug(
                    "autonomous_skip detectado (agent=%s, chat_id=%s)",
                    self._settings.id,
                    chat_id_str,
                )
                return

            await send_html_or_plain(
                lambda text, pm: self._app.bot.send_message(
                    chat_id=chat_id_int, text=text, parse_mode=pm
                ),
                response,
            )

            asyncio.ensure_future(
                self._emit_event(
                    event_type="assistant_response",
                    chat_id=chat_id_str,
                    content=response,
                )
            )

        except Exception as exc:
            logger.exception(
                "Error procesando flush de grupo (agent=%s, chat_id=%s)",
                self._settings.id,
                chat_id_str,
            )
            try:
                await self._app.bot.send_message(chat_id=chat_id_int, text=f"Error: {exc}")
            except Exception:
                pass
        finally:
            self._ports.run_agent.set_extra_system_sections([])
