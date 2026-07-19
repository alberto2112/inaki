"""Puente broadcast del TelegramBot: emisión de eventos al LAN y trigger de ingress.

Mixin de ``TelegramBot``. La emisión respeta los flags ``broadcast.emit.*``;
el ingress (``_on_broadcast_received``) persiste SIEMPRE y rate-limita solo
el flush (ver ``group_flow``)."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from core.ports.outbound.broadcast_port import BroadcastMessage


if TYPE_CHECKING:
    from collections.abc import Callable

    from adapters.inbound.telegram.ports import TelegramBotPorts, TelegramBotSettings
    from core.ports.outbound.broadcast_port import BroadcastEmitter, BroadcastReceiver

logger = logging.getLogger(__name__)


def _format_history_prefix(msg: BroadcastMessage) -> str:
    """Construye el contenido a persistir en historial según el ``event_type`` del broadcast.

    Pure function — testeable de forma aislada y reusable desde el callback de
    ingress (``_on_broadcast_received``).

    Reglas:
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


class TelegramBroadcastMixin:
    """Emisión gated por config + suscripción/manejo del trigger broadcast."""

    # Contrato con TelegramBot — estado y colaboradores que este mixin consume.
    _settings: TelegramBotSettings
    _ports: TelegramBotPorts
    _behavior: str
    _allowed_chat_ids: list[str]
    _broadcast_emitter: BroadcastEmitter | None
    _broadcast_receiver: BroadcastReceiver | None
    _emit_flags: dict[str, bool]
    _rate_limiter: Any
    _rate_limit_max: int
    _schedule_group_flush: Callable[[str, str], None]

    async def _emit_event(
        self,
        *,
        event_type: str,
        chat_id: str,
        content: str,
        sender: str = "",
    ) -> None:
        """Emite un evento broadcast respetando el flag de config para ese event_type.

        Centraliza la decisión de emitir o no — los handlers sólo declaran QUÉ
        evento corresponde a su flujo, sin replicar lógica de gating.

        Reglas:
        - Si ``broadcast_emitter`` no está configurado → no-op silencioso.
        - Si el flag ``emit.{event_type}`` está en ``False`` → no-op silencioso.
        - Si ``content.strip()`` es vacío → no-op silencioso (mismo patrón que el
          voice handler post-transcripción).
        - Caso normal → construye ``BroadcastMessage`` y llama ``emitter.emit``
          como fire-and-forget (excepciones loggeadas, no propagadas).

        Args:
            event_type: ``"assistant_response"``, ``"user_input_voice"`` o
                ``"user_input_photo"``.
            chat_id: ID del chat de origen como string.
            content: Texto del evento (respuesta del LLM, transcripción o descripción).
            sender: Nombre del humano emisor — solo aplica a eventos ``user_input_*``;
                vacío para ``assistant_response``.
        """
        if self._broadcast_emitter is None:
            return
        if not self._emit_flags.get(event_type, False):
            return
        if not content.strip():
            return

        msg = BroadcastMessage(
            timestamp=time.time(),
            agent_id=self._settings.id,
            chat_id=chat_id,
            event_type=event_type,  # type: ignore[arg-type]
            content=content,
            sender=sender,
        )
        try:
            await self._broadcast_emitter.emit(msg)
        except Exception as exc:
            logger.warning(
                "Fallo al emitir broadcast event_type=%s (agent=%s, chat_id=%s): %s",
                event_type,
                self._settings.id,
                chat_id,
                exc,
            )

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
                self._settings.id,
                self._behavior,
            )
            return
        await self._broadcast_receiver.subscribe(self._on_broadcast_received)
        logger.info(
            "Bot '%s': suscripto a broadcast como trigger (autonomous)",
            self._settings.id,
        )

    async def _on_broadcast_received(self, msg: BroadcastMessage) -> None:
        """Callback invocado por el adapter por cada ``BroadcastMessage`` válido.

        En el flujo unificado, un broadcast se trata como un mensaje más entrante
        al chat: se persiste SIEMPRE con prefijo ``<agent_id> said: ...`` y luego
        se decide si programar un flush task. Si el rate limiter hace breach, el
        broadcast queda guardado en historial pero NO se programa respuesta —
        cuando despierte el flush activo (o llegue un trigger posterior), el
        batch acumulado va a ser leído íntegro.

        Mismo orden que ``_handle_group_message`` para mensajes humanos: persistir
        primero, rate-limitar solo el flush.

        Silencioso y defensivo: cualquier excepción queda aquí.
        """
        try:
            # Autorización del scope — MISMA matriz que ``_is_authorized`` para los
            # updates nativos de Telegram (ver ``telegram-group-auth``). Un broadcast
            # llega por TCP sin ``Update``, así que jamás pasa por ``_is_authorized``:
            # sin este guard, el bot persiste y agenda flush para grupos donde ya no
            # es miembro (respuestas que dan ``Forbidden`` pero ensucian el historial).
            # ``allowed_chat_ids`` es la única fuente de verdad de "dónde vive el bot":
            # NO se duplica en la config de broadcast (broadcast = transporte puro,
            # ver ``groups-vs-broadcast``). Lista vacía → no responde en grupos →
            # ignora todo broadcast, coherente con la regla del path nativo.
            if str(msg.chat_id) not in self._allowed_chat_ids:
                logger.info(
                    "broadcast.trigger.skip.unauthorized_chat agent=%s from=%s chat_id=%s",
                    self._settings.id,
                    msg.agent_id,
                    msg.chat_id,
                )
                return

            preview = msg.content[:200].replace("\n", " ")
            logger.info(
                "broadcast.trigger.eval agent=%s from=%s chat_id=%s preview=%r",
                self._settings.id,
                msg.agent_id,
                msg.chat_id,
                preview,
            )

            contenido = _format_history_prefix(msg)
            await self._ports.run_agent.record_user_message(
                contenido,
                channel="telegram",
                chat_id=msg.chat_id,
            )

            # Rate limiter por (agent_id, chat_id) — evita tormentas bot-to-bot.
            # Solo aplica a ``assistant_response`` (único event_type que puede
            # producir loops entre bots). Los ``user_input_*`` vienen del humano
            # y no deben consumir el contador ni gatillar breach.
            # Gobierna SOLO el flush: el broadcast ya quedó persistido arriba.
            if self._rate_limiter is not None and msg.event_type == "assistant_response":
                breach = self._rate_limiter.check_and_increment(
                    self._settings.id,
                    msg.chat_id,
                    self._rate_limit_max,
                )
                if breach is not None:
                    logger.info(
                        "broadcast.trigger.skip.rate_limited agent=%s chat_id=%s counter=%d",
                        self._settings.id,
                        msg.chat_id,
                        breach.counter,
                    )
                    return

            self._schedule_group_flush(msg.chat_id, "supergroup")
        except Exception:
            logger.exception(
                "Error procesando broadcast (agent=%s, from=%s, chat_id=%s)",
                self._settings.id,
                msg.agent_id,
                msg.chat_id,
            )
