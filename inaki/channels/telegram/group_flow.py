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

from telegram import Bot, Update, User

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

        # Lo que se le DIRIGIÓ al agente desde el último flush, por chat_id: la
        # query del turno (``execute(query=...)``). El historial trae además todo
        # lo persistido sin turno (``listen``/``mention`` sin mención) como
        # contexto, pero eso no debe diluir el embedding del routing — e5 trunca
        # y se quedaría con lo más viejo. El flush la consume entera.
        self.consultas: dict[str, list[str]] = {}

    def es_para_otro(self, message) -> bool:
        """``True`` si el mensaje apunta a un destinatario concreto que NO es este bot.

        Reply a un bot ≡ mención implícita. Esos mensajes se descartan enteros (ni
        se persisten): son conversación de otro. Los broadcasts no pasan por acá.
        """
        return bool(
            self.bot_username
            and hay_destinatario_explicito(message)
            and not dirigido_a(message, self.bot_username)
        )

    def dispara_turno(self, message) -> bool:
        """Decide si un mensaje de grupo ya persistido dispara un turno del LLM.

        Persistir y disparar son dos decisiones: en ``listen`` y en ``mention``
        (sin mención) el mensaje se guarda igual en el historial SIN turno, para
        que cuando por fin se le hable al agente tenga el contexto del grupo.

        - ``listen`` → nunca.
        - ``mention`` → solo si el mensaje está dirigido al bot (mención o reply).
        - ``autonomous`` → siempre (el rate limiter decide después).
        """
        if self._behavior == "autonomous":
            return True
        if self._behavior != "mention":
            return False
        if not self.bot_username:
            logger.warning(
                "behavior='mention' pero bot_username no configurado (agent=%s) — sin turno",
                self._agent_id,
            )
            return False
        return dirigido_a(message, self.bot_username)

    async def handle_message(
        self, update: Update, user_input: str, chat_type: str, *, preformatted: bool = False
    ) -> None:
        """Maneja mensajes de chats grupales según el behavior configurado.

        Flujo:
        1. Descartar lo dirigido explícitamente a otro (``es_para_otro``).
        2. Persistir el mensaje en el historial via ``record_user_message`` —
           SIEMPRE, en los tres behaviors: lo que se dijo en el grupo es contexto
           aunque no dispare turno.
        3. Si ``dispara_turno`` → ``disparar`` (reacción, rate limiter, flush).

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
        chat_id_str = str(chat.id)

        # La autorización del grupo (allowed_chat_ids) ya se resolvió upstream en
        # ``TelegramAuth.is_authorized`` — todos los handlers de mensaje la
        # chequean antes de llegar acá. No se repite el check.
        if self.es_para_otro(update.message):
            return

        if preformatted:
            contenido_grupo = f"{extract_sender_name(update.message)} sent:\n{user_input}"
        else:
            contenido_grupo = format_group_message(update.message)
        await self._history.record_user_message(
            contenido_grupo, channel="telegram", chat_id=chat_id_str
        )

        if self.dispara_turno(update.message):
            await self.disparar(update, chat_type, consulta=contenido_grupo)

    async def disparar(self, update: Update, chat_type: str, *, consulta: str) -> None:
        """Dispara (o se suma a) el turno de grupo por un mensaje YA persistido.

        Lo llaman ``handle_message`` y los handlers de media (audio, foto), que
        persisten su propio bloque antes. Flujo:

        1. Snapshot del emisor (``last_sender``) — solo de mensajes que disparan:
           ``{{CHANNEL.SENDER}}`` es quien le habló al agente, no el último que
           habló en el grupo.
        2. Acumular ``consulta`` en la query del próximo flush (solo ``mention``).
        3. Reaccionar 👀 (confirma al usuario que lo leíste).
        4. Rate limiter (solo en autonomous): si el sender es humano, resetea el
           limitador primero. Si hay breach, se sale sin programar respuesta pero
           el mensaje ya quedó guardado, reaccionado y acumulado.
        5. Programar un flush task si no hay uno corriendo. Mensajes que lleguen
           dentro de la ventana de delay se acumulan en el historial y se procesan
           todos juntos en un único turno cuando el delay vence.
        """
        chat = update.effective_chat
        if chat is None:
            return
        chat_id_str = str(chat.id)

        # Bots (broadcasts vía ingress, no por acá) no actualizan este dict —
        # solo humanos que escriben en el grupo.
        from_user = getattr(update.message, "from_user", None)
        if from_user is not None and not getattr(from_user, "is_bot", False):
            self.last_sender[chat_id_str] = {
                "sender_name": _safe_optional_str(compose_sender_identity(update.message)),
                "username": _safe_optional_str(getattr(from_user, "username", None)),
                "first_name": _safe_optional_str(getattr(from_user, "first_name", None)),
                "last_name": _safe_optional_str(getattr(from_user, "last_name", None)),
            }
        # Solo en ``mention`` el batch mezcla contexto y consulta. En ``autonomous``
        # todo lo persistido dispara (incluidos los broadcasts del ingress, que no
        # pasan por acá): el trailing batch entero ya ES la consulta.
        if self._behavior == "mention":
            self.consultas.setdefault(chat_id_str, []).append(consulta)
        await self._reactions.react_group(update, "👀")

        if self._behavior == "autonomous" and self._rate_limit.enabled:
            sender = update.message.from_user if update.message is not None else None
            if sender and not sender.is_bot:
                # Habló un humano: re-armado inmediato. Va ANTES del gate, así
                # el mismo mensaje que levanta el cooldown ya puede responderse.
                self._rate_limit.reset(chat_id_str)

            enfriando = self._rate_limit.cooldown(chat_id_str)
            if enfriando is not None:
                logger.debug(
                    "Cooldown activo en grupo (agent=%s, chat_id=%s, consecutivas=%d, "
                    "retry_in=%.1fs)",
                    self._agent_id,
                    chat_id_str,
                    enfriando.consecutive,
                    enfriando.retry_in,
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

        Lo que el agente EMITE cuenta como intervención (``record_response``):
        el presupuesto se gasta al hablar, no al recibir. Un turno que termina
        en ``__SKIP__`` no cuenta, y los N mensajes que se coalescieron en este
        flush son UNA sola intervención.
        """
        delay = random.uniform(self._min_delay, self._max_delay)
        logger.debug(
            "group_response_delay agent=%s chat_id=%s delay=%.2fs",
            self._agent_id,
            chat_id_str,
            delay,
        )
        await asyncio.sleep(delay)
        # Sin consultas acumuladas (flush de un broadcast entrante) → ``None``: el
        # kernel deriva la query del trailing batch, como siempre.
        consulta = "\n".join(self.consultas.pop(chat_id_str, [])) or None
        respondio = await self._turns.run_group(
            chat_id_str, chat_type, self.last_sender.get(chat_id_str, {}), consulta=consulta
        )
        if respondio and self._behavior == "autonomous":
            self._rate_limit.record_response(chat_id_str)

    async def resolve_bot_username(self, bot: Bot) -> User | None:
        """Obtiene y valida el username del bot contra la API de Telegram.

        Llama a ``get_me()`` UNA SOLA VEZ al arranque. No bloquea ni falla el startup:
        - Si ``bot_username`` no está en config → se auto-detecta para que los filtros funcionen.
        - Si el username real difiere del configurado → WARNING (no bloquea).
        - Si ``get_me()`` falla → WARNING (no bloquea).

        Devuelve el ``User`` del bot (o ``None`` si ``get_me()`` falló) para que el
        canal derive de ahí su rótulo de broadcast sin un segundo ``get_me()``.
        """
        try:
            me = await bot.get_me()
        except Exception as exc:
            logger.warning(
                "Telegram bot '%s': no se pudo obtener bot info via get_me(): %s",
                self._agent_id,
                exc,
            )
            return None

        real_username = me.username  # puede ser None si el bot no tiene username

        if real_username is None:
            logger.warning(
                "Telegram bot '%s': get_me() devolvió username=None. "
                "Los filtros de reply y mención no funcionarán correctamente.",
                self._agent_id,
            )
            return me

        if self.bot_username is None:
            self.bot_username = real_username
            logger.info(
                "Telegram bot '%s': bot_username auto-detectado: @%s",
                self._agent_id,
                real_username,
            )
            return me

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
        return me
