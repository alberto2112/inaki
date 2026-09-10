"""Política de rate limit de grupos autónomos: el ÚNICO estado mutable en runtime del bot.

Envuelve el ``FixedWindowRateLimiter`` (transporte, por ventana) con la política
del agente: cuántas respuestas por ventana y cuáles eran los valores de config.
``/ratelimit`` lo MUTA; el flujo de grupos y el ingress de broadcast lo LEEN.
Antes eran cuatro atributos sueltos del bot que un mixin escribía y otros dos
leían sin que ningún constructor lo dijera.

Sin limiter (``None``: el agente no tiene transporte de broadcast) todo es
no-op: ``check`` nunca hace breach y ``reset`` no hace nada.
"""

from __future__ import annotations

import logging

from inaki.channels.telegram.broadcast.rate_limiter import BreachSignal, FixedWindowRateLimiter

logger = logging.getLogger(__name__)


class GroupRateLimit:
    def __init__(
        self,
        limiter: FixedWindowRateLimiter | None,
        *,
        agent_id: str,
        max_count: int,
        window_seconds: int,
    ) -> None:
        self.limiter = limiter
        self._agent_id = agent_id
        self.max_count = max_count
        # Defaults preservados desde config para ``/ratelimit reset``. Las
        # mutaciones en runtime NO se persisten: al reiniciar se vuelven a leer.
        self.default_max_count = max_count
        self.default_window_seconds = window_seconds

    @property
    def enabled(self) -> bool:
        return self.limiter is not None

    @property
    def window_seconds(self) -> int:
        if self.limiter is None:
            return self.default_window_seconds
        return int(self.limiter.window_seconds)

    def reset(self, chat_id: str) -> None:
        """Señal "habló un humano": la ventana del chat arranca de cero."""
        if self.limiter is not None:
            self.limiter.reset(self._agent_id, chat_id)

    def check(self, chat_id: str) -> BreachSignal | None:
        """Consume un cupo del chat; devuelve la señal de breach si se pasó."""
        if self.limiter is None:
            return None
        return self.limiter.check_and_increment(self._agent_id, chat_id, self.max_count)

    def set(self, count: int, window_seconds: int | None = None) -> None:
        """Override en memoria (``/ratelimit <count> [window]``)."""
        self.max_count = count
        if window_seconds is not None and self.limiter is not None:
            self.limiter.set_window(float(window_seconds))
        logger.info(
            "ratelimit.update agent=%s count=%d window=%ds",
            self._agent_id,
            self.max_count,
            self.window_seconds,
        )

    def restore_defaults(self) -> None:
        """``/ratelimit reset``: vuelve a los valores de config."""
        self.max_count = self.default_max_count
        if self.limiter is not None:
            self.limiter.set_window(float(self.default_window_seconds))
        logger.info(
            "ratelimit.reset agent=%s count=%d window=%ds",
            self._agent_id,
            self.max_count,
            self.default_window_seconds,
        )
