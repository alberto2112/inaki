"""Flujo de grupos: routing por behavior, buffer-delay-coalesce y flush.

Dueño del estado de los grupos: ``pending_tasks`` (un flush task por chat),
``last_sender`` (último emisor humano por chat) y ``bot_username`` (el único
consumidor del username es la detección de menciones, así que la validación
contra ``get_me()`` vive acá: ``resolve_bot_username``).
"""

from __future__ import annotations

import asyncio
import logging
import random

from telegram import Bot, Update

from inaki.channels.telegram.message_mapper import (
    _safe_optional_str,
    compose_sender_identity,
    dirigido_a,
    extract_sender_name,
    format_group_message,
    hay_destinatario_explicito,
)
from inaki.channels.telegram.rate_limit import GroupRateLimit
from inaki.channels.telegram.reactions import Reactions
from inaki.channels.telegram.turn import TurnRunner
from inaki.kernel.conversation_history import ConversationHistory

logger = logging.getLogger(__name__)


class GroupFlow:
    def __init__(
        self,
        *,
        history: ConversationHistory,
        agent_id: str,
        behavior: str,
        bot_username: str | None,
        min_delay: float,
        max_delay: float,
        reactions: Reactions,
        rate_limit: GroupRateLimit,
        turns: TurnRunner,
    ) -> None:
        self._history = history
        self._agent_id = agent_id
        self._behavior = behavior
        self.bot_username = bot_username
        self._min_delay = min_delay
        self._max_delay = max_delay
        self._reactions = reactions
        self._rate_limit = rate_limit
        self._turns = turns

        # Tasks de flush por chat_id. Cada chat tiene a lo sumo uno corriendo:
        # mientras está vivo, los mensajes que lleguen se acumulan en el historial
        # vía record_user_message y se procesan todos juntos cuando el delay vence.
        self.pending_tasks: dict[str, asyncio.Task] = {}

        # Último emisor humano por chat_id. Se actualiza en cada paso por
        # ``handle_message`` y se lee al flushear para resolver
        # ``{{CHANNEL.SENDER}}/USERNAME/FIRST_NAME/LAST_NAME}}``. Heurística: el
        # más reciente del batch gana. In-memory; un restart lo pierde (mismo
        # trade-off que ``pending_tasks``). No se limpia tras el flush: la próxima
        # ronda lo sobreescribe, y mientras tanto refleja "la última persona que
        # habló en este chat".
        self.last_sender: dict[str, dict[str, str | None]] = {}

    async def handle_message(
        self, update: Update, user_input: str, chat_type: str, *, preformatted: bool = False
    ) -> None:
        """Maneja mensajes de chats grupales según el behavior configurado.

        Flujo:
        1. Filtros: behavior, destinatario explícito, mention check.
        2. Persistir el mensaje en el historial via ``record_user_message``.
        3. Reaccionar 👀 (confirma al usuario que lo leíste).
        4. Rate limiter (solo en autonomous): si el sender es humano, resetea el
           limitador primero. Si hay breach, se sale sin programar respuesta pero
           el mensaje ya quedó guardado y reaccionado.
        5. Programar un flush task si no hay uno corriendo. Mensajes que lleguen
           dentro de la ventana de delay se acumulan en el historial y se procesan
           todos juntos en un único turno cuando el delay vence.

        Args:
            preformatted: ``True`` cuando ``user_input`` ya viene formateado por
                el caller (bloques de attachments de media). Se persiste tal
                cual con el prefijo del sender. Con ``False`` (texto plano), el
                contenido se deriva de ``update.message`` vía
                ``format_group_message`` — que lee ``message.text`` y devolvería
                vacío para un media (bug histórico: los álbumes en grupo
                quedaban como ``"marta said: "``).
        """
        chat = update.effective_chat
        if chat is None:
            return
        chat_id = chat.id
        chat_id_str = str(chat_id)

        # La autorización del grupo (allowed_chat_ids) ya se resolvió upstream en
        # ``TelegramAuth.is_authorized`` — todos los handlers de mensaje la
        # chequean antes de llegar acá. No se repite el check.
        behavior = self._behavior

        if behavior == "listen":
            return

        # Filtro unificado de destinatario explícito. Reply a un bot ≡ mención
        # implícita. Si el mensaje apunta a alguien concreto y ese alguien NO
        # soy yo → ignorar. Los broadcasts no pasan por aquí.
        if (
            self.bot_username
            and hay_destinatario_explicito(update.message)
            and not dirigido_a(update.message, self.bot_username)
        ):
            return

        if behavior == "mention":
            if not self.bot_username:
                logger.warning(
                    "behavior='mention' pero bot_username no configurado (agent=%s) — ignorando",
                    self._agent_id,
                )
                return
            if not dirigido_a(update.message, self.bot_username):
                return

        if preformatted:
            contenido_grupo = f"{extract_sender_name(update.message)} sent:\n{user_input}"
        else:
            contenido_grupo = format_group_message(update.message)
        await self._history.record_user_message(
            contenido_grupo, channel="telegram", chat_id=chat_id_str
        )
        # Snapshot del último emisor humano del chat. Se hace SIEMPRE (mention o
        # autonomous): la heurística es "quien acaba de hablar" sin distinguir
        # behavior. Bots (broadcasts vía ingress, no por acá) no actualizan este
        # dict — solo humanos que escriben en el grupo.
        from_user = getattr(update.message, "from_user", None)
        if from_user is not None and not getattr(from_user, "is_bot", False):
            self.last_sender[chat_id_str] = {
                "sender_name": _safe_optional_str(compose_sender_identity(update.message)),
                "username": _safe_optional_str(getattr(from_user, "username", None)),
                "first_name": _safe_optional_str(getattr(from_user, "first_name", None)),
                "last_name": _safe_optional_str(getattr(from_user, "last_name", None)),
            }
        await self._reactions.react_group(update, "👀")

        if behavior == "autonomous" and self._rate_limit.enabled:
            sender = update.message.from_user if update.message is not None else None
            if sender and not sender.is_bot:
                self._rate_limit.reset(chat_id_str)

            breach = self._rate_limit.check(chat_id_str)
            if breach is not None:
                logger.debug(
                    "Rate limit alcanzado en grupo (agent=%s, chat_id=%s, counter=%d)",
                    self._agent_id,
                    chat_id,
                    breach.counter,
                )
                return

        self.schedule_flush(chat_id_str, chat_type)

    def schedule_flush(self, chat_id_str: str, chat_type: str) -> None:
        """Crea un task de flush si no hay uno activo para este chat.

        Si ya hay uno corriendo, el mensaje recién persistido será visto por ese
        task cuando despierte — no creamos uno nuevo. Idempotente.
        """
        task = self.pending_tasks.get(chat_id_str)
        if task is None or task.done():
            self.pending_tasks[chat_id_str] = asyncio.create_task(
                self.flush_buffer(chat_id_str, chat_type)
            )

    async def flush_buffer(self, chat_id_str: str, chat_type: str) -> None:
        """Espera el delay aleatorio y dispara el turno de grupo para este chat.

        El turno lee el historial vía ``execute()`` sin user_input — la query
        se deriva del trailing batch de role=user del historial.
        """
        delay = random.uniform(self._min_delay, self._max_delay)
        logger.debug(
            "group_response_delay agent=%s chat_id=%s delay=%.2fs",
            self._agent_id,
            chat_id_str,
            delay,
        )
        await asyncio.sleep(delay)
        await self._turns.run_group(chat_id_str, chat_type, self.last_sender.get(chat_id_str, {}))

    async def resolve_bot_username(self, bot: Bot) -> None:
        """Obtiene y valida el username del bot contra la API de Telegram.

        Llama a ``get_me()`` UNA SOLA VEZ al arranque. No bloquea ni falla el startup:
        - Si ``bot_username`` no está en config → se auto-detecta para que los filtros funcionen.
        - Si el username real difiere del configurado → WARNING (no bloquea).
        - Si ``get_me()`` falla → WARNING (no bloquea).
        """
        try:
            me = await bot.get_me()
        except Exception as exc:
            logger.warning(
                "Telegram bot '%s': no se pudo obtener bot info via get_me(): %s",
                self._agent_id,
                exc,
            )
            return

        real_username = me.username  # puede ser None si el bot no tiene username

        if real_username is None:
            logger.warning(
                "Telegram bot '%s': get_me() devolvió username=None. "
                "Los filtros de reply y mención no funcionarán correctamente.",
                self._agent_id,
            )
            return

        if self.bot_username is None:
            self.bot_username = real_username
            logger.info(
                "Telegram bot '%s': bot_username auto-detectado: @%s",
                self._agent_id,
                real_username,
            )
            return

        if real_username.lower() != self.bot_username.lower():
            logger.warning(
                "Telegram bot '%s': bot_username en config ('%s') no coincide "
                "con el username real del bot ('@%s'). "
                "Actualizá groups.bot_username en la config para evitar fallos en mention detection.",
                self._agent_id,
                self.bot_username,
                real_username,
            )
        else:
            logger.info(
                "Telegram bot '%s': bot_username validado correctamente ('@%s')",
                self._agent_id,
                real_username,
            )
