"""Ingress del broadcast: un mensaje llegado por el LAN se trata como uno más del chat.

Se persiste SIEMPRE con su prefijo y se rate-limita solo el flush (ver
``group_flow``). Solo se suscribe en ``behavior: autonomous``: ``listen`` no
responde nunca y ``mention`` requiere una mención real de Telegram, que no
existe en un mensaje llegado por TCP.
"""

from __future__ import annotations

import logging

from inaki.channels.telegram.auth import TelegramAuth
from inaki.channels.telegram.broadcast.port import BroadcastMessage, BroadcastReceiver
from inaki.channels.telegram.group_flow import GroupFlow
from inaki.channels.telegram.rate_limit import GroupRateLimit
from inaki.kernel.use_cases.conversation_history import ConversationHistory

logger = logging.getLogger(__name__)


def _format_history_prefix(msg: BroadcastMessage) -> str:
    """Construye el contenido a persistir en historial según el ``event_type``.

    - ``assistant_response`` → ``"{agent_id} said: {content}"`` (backward-compat).
    - ``user_input_voice``   → ``"{sender} (audio): {content}"``.
    - ``user_input_photo``   → ``"{sender} (foto): {content}"``.
    """
    if msg.event_type == "user_input_voice":
        return f"{msg.sender} (audio): {msg.content}"
    if msg.event_type == "user_input_photo":
        return f"{msg.sender} (foto): {msg.content}"
    # assistant_response (default)
    return f"{msg.agent_id} said: {msg.content}"


class BroadcastIngress:
    def __init__(
        self,
        *,
        receiver: BroadcastReceiver | None,
        history: ConversationHistory,
        agent_id: str,
        behavior: str,
        auth: TelegramAuth,
        rate_limit: GroupRateLimit,
        groups: GroupFlow,
    ) -> None:
        self._receiver = receiver
        self._history = history
        self._agent_id = agent_id
        self._behavior = behavior
        self._auth = auth
        self._rate_limit = rate_limit
        self._groups = groups

    async def subscribe(self) -> None:
        """Registra el callback de ingress (solo autonomous con receiver)."""
        if self._receiver is None:
            return
        if self._behavior != "autonomous":
            logger.debug(
                "Bot '%s': behavior=%s — no se registra trigger de broadcast",
                self._agent_id,
                self._behavior,
            )
            return
        await self._receiver.subscribe(self.on_received)
        logger.info("Bot '%s': suscripto a broadcast como trigger (autonomous)", self._agent_id)

    async def on_received(self, msg: BroadcastMessage) -> None:
        """Callback invocado por el adapter por cada ``BroadcastMessage`` válido.

        Se persiste SIEMPRE con prefijo ``<agent_id> said: ...`` y luego se decide
        si programar un flush task. Si el rate limiter hace breach, el broadcast
        queda guardado en historial pero NO se programa respuesta — cuando
        despierte el flush activo (o llegue un trigger posterior), el batch
        acumulado va a ser leído íntegro. Mismo orden que ``GroupFlow.handle_message``
        para mensajes humanos: persistir primero, rate-limitar solo el flush.

        Silencioso y defensivo: cualquier excepción queda aquí.
        """
        try:
            # Autorización del scope — MISMA matriz que ``is_authorized`` para los
            # updates nativos (ver ``telegram-group-auth``). Un broadcast llega por
            # TCP sin ``Update``: sin este guard, el bot persiste y agenda flush
            # para grupos donde ya no es miembro. ``allowed_chat_ids`` es la única
            # fuente de verdad de "dónde vive el bot" (broadcast = transporte puro,
            # ver ``groups-vs-broadcast``). Lista vacía → ignora todo broadcast.
            if not self._auth.is_allowed_chat(msg.chat_id):
                logger.info(
                    "broadcast.trigger.skip.unauthorized_chat agent=%s from=%s chat_id=%s",
                    self._agent_id,
                    msg.agent_id,
                    msg.chat_id,
                )
                return

            preview = msg.content[:200].replace("\n", " ")
            logger.info(
                "broadcast.trigger.eval agent=%s from=%s chat_id=%s preview=%r",
                self._agent_id,
                msg.agent_id,
                msg.chat_id,
                preview,
            )

            await self._history.record_user_message(
                _format_history_prefix(msg), channel="telegram", chat_id=msg.chat_id
            )

            # Rate limiter por (agent_id, chat_id) — evita tormentas bot-to-bot.
            # Solo ``assistant_response`` consume el contador (único event_type que
            # puede producir loops entre bots). Los ``user_input_*`` vienen de un
            # humano: no consumen y además RESETEAN, igual que un mensaje humano
            # nativo. La señal es "habló un humano", da igual por qué transporte
            # llegó (``broadcast-human-reset``). Gobierna SOLO el flush: el
            # broadcast ya quedó persistido arriba.
            if msg.event_type.startswith("user_input_"):
                self._rate_limit.reset(msg.chat_id)

            if msg.event_type == "assistant_response":
                breach = self._rate_limit.check(msg.chat_id)
                if breach is not None:
                    logger.info(
                        "broadcast.trigger.skip.rate_limited agent=%s chat_id=%s counter=%d",
                        self._agent_id,
                        msg.chat_id,
                        breach.counter,
                    )
                    return

            self._groups.schedule_flush(msg.chat_id, "supergroup")
        except Exception:
            logger.exception(
                "Error procesando broadcast (agent=%s, from=%s, chat_id=%s)",
                self._agent_id,
                msg.agent_id,
                msg.chat_id,
            )
