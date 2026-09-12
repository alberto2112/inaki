"""Política de rate limit de grupos autónomos: el ÚNICO estado mutable en runtime del bot.

El contrato es **por intervenciones consecutivas sin humano**, no por caudal:

- El agente puede responder en un grupo hasta ``max_count`` veces seguidas.
- La intervención ``max_count`` dispara un **cooldown** de ``window_seconds``
  contado DESDE esa intervención: mientras dure, el agente no vuelve a responder
  en ese chat.
- Un humano (nativo o ``user_input_*`` por broadcast) **re-arma al instante**:
  ``reset`` pone el contador en cero y levanta el cooldown.

Lo que cuenta es lo que el agente EMITE (``record_response``), no lo que le
llega: un turno que termina en ``__SKIP__`` no gasta presupuesto, y N mensajes
coalescidos en un flush son UNA intervención.

``/ratelimit`` muta la política en runtime; el flujo de grupos y el ingress de
broadcast la consultan con ``cooldown`` antes de programar un flush.

Deshabilitada (``enabled=False``: el agente no está en ``behavior: autonomous``)
todo es no-op: ``cooldown`` nunca bloquea y ``record_response`` no cuenta.
"""

from __future__ import annotations

import logging
import time as _time_module
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CooldownSignal:
    """Señal de "estoy en cooldown" para el call-site que iba a responder."""

    agent_id: str
    chat_id: str
    consecutive: int
    """Intervenciones consecutivas que dispararon el cooldown."""
    retry_in: float
    """Segundos que faltan para el re-armado por tiempo. Un humano lo acorta a 0."""


@dataclass
class _EstadoChat:
    """Estado por chat: cuántas seguidas van y hasta cuándo dura el cooldown."""

    consecutive: int = 0
    cooldown_hasta: float | None = None


class GroupRateLimit:
    def __init__(
        self,
        *,
        enabled: bool,
        agent_id: str,
        max_count: int,
        window_seconds: int,
        _now: Callable[[], float] = _time_module.monotonic,
    ) -> None:
        self._enabled = enabled
        self._agent_id = agent_id
        self._now = _now
        self._estados: dict[str, _EstadoChat] = {}
        self.max_count = max_count
        self._window_seconds = float(window_seconds)
        # Defaults preservados desde config para ``/ratelimit reset``. Las
        # mutaciones en runtime NO se persisten: al reiniciar se vuelven a leer.
        self.default_max_count = max_count
        self.default_window_seconds = window_seconds

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def window_seconds(self) -> int:
        return int(self._window_seconds)

    def reset(self, chat_id: str) -> None:
        """Señal "habló un humano": contador a cero y cooldown levantado.

        Es el re-armado inmediato del contrato. Se invoca desde los DOS
        transportes (nativo y broadcast): la señal es "habló un humano", da igual
        por qué puerta entró (``broadcast-human-reset``).
        """
        if not self._enabled:
            return
        if self._estados.pop(chat_id, None) is not None:
            logger.debug("ratelimit.reset.humano agent=%s chat_id=%s", self._agent_id, chat_id)

    def cooldown(self, chat_id: str) -> CooldownSignal | None:
        """¿Puedo responder en este chat? ``None`` = sí. NO tiene efectos.

        El re-armado por tiempo es perezoso: se resuelve acá, al preguntar, sin
        timers ni tareas de fondo.
        """
        if not self._enabled:
            return None
        estado = self._estados.get(chat_id)
        if estado is None or estado.cooldown_hasta is None:
            return None
        restante = estado.cooldown_hasta - self._now()
        if restante <= 0:
            # Cooldown vencido: se re-arma solo, con el contador en cero.
            del self._estados[chat_id]
            logger.info(
                "ratelimit.rearme.por_tiempo agent=%s chat_id=%s window=%ds",
                self._agent_id,
                chat_id,
                self.window_seconds,
            )
            return None
        return CooldownSignal(
            agent_id=self._agent_id,
            chat_id=chat_id,
            consecutive=estado.consecutive,
            retry_in=restante,
        )

    def record_response(self, chat_id: str) -> None:
        """El agente ACABA de responder en el grupo: cuenta la intervención.

        Al llegar a ``max_count`` seguidas arranca el cooldown desde este
        instante — no desde el primer mensaje de una ventana de pared. Esa es la
        diferencia que corta el loop bot-a-bot: el reloj lo arranca la
        intervención del agente, no el ritmo que impone el otro bot.
        """
        if not self._enabled:
            return
        estado = self._estados.setdefault(chat_id, _EstadoChat())
        estado.consecutive += 1
        if estado.consecutive >= self.max_count:
            estado.cooldown_hasta = self._now() + self._window_seconds
            logger.info(
                "ratelimit.cooldown agent=%s chat_id=%s consecutive=%d window=%ds",
                self._agent_id,
                chat_id,
                estado.consecutive,
                self.window_seconds,
            )

    def set(self, count: int, window_seconds: int | None = None) -> None:
        """Override en memoria (``/ratelimit <count> [window]``).

        Aplica a los cooldowns que se disparen DESPUÉS: los ya abiertos
        conservan su vencimiento (mismo criterio que tenía ``set_window``).
        """
        self.max_count = count
        if window_seconds is not None:
            if window_seconds <= 0:
                raise ValueError(f"window_seconds debe ser > 0, recibido: {window_seconds}")
            self._window_seconds = float(window_seconds)
        logger.info(
            "ratelimit.update agent=%s count=%d window=%ds",
            self._agent_id,
            self.max_count,
            self.window_seconds,
        )

    def restore_defaults(self) -> None:
        """``/ratelimit reset``: vuelve a los valores de config."""
        self.max_count = self.default_max_count
        self._window_seconds = float(self.default_window_seconds)
        logger.info(
            "ratelimit.reset agent=%s count=%d window=%ds",
            self._agent_id,
            self.max_count,
            self.default_window_seconds,
        )
