"""
FixedWindowRateLimiter — limitador de tasa por ventana fija para broadcast.

Controla cuántas veces puede emitir un agente (``agent_id``) en un chat
(``chat_id``) dentro de una ventana de tiempo fija. Cuando se supera el
límite, retorna una señal de breach con el tiempo restante hasta el reset.

Asunción de threading: corre en un único event loop de asyncio — sin locks.
"""

from __future__ import annotations

import time as _time_module
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class BreachSignal:
    """Señal emitida cuando un agente supera el límite de tasa.

    Contiene información suficiente para que el llamador construya un
    mensaje descriptivo para el LLM o para el log.
    """

    agent_id: str
    """Agente que superó el límite."""

    chat_id: str
    """Chat donde se produjo el breach."""

    counter: int
    """Contador acumulado en la ventana actual al momento del breach."""

    retry_in: float
    """Segundos hasta el reset de la ventana. Puede ser 0.0 si la ventana
    ya expiró (raza entre check y reset)."""


class FixedWindowRateLimiter:
    """Limitador de tasa por ventana fija.

    El estado interno es un dict keyed por ``(agent_id, chat_id)`` con
    valor ``(window_start_ts, counter)``. La ventana se reinicia cada vez
    que han transcurrido ``window_seconds`` desde ``window_start_ts``.

    Parámetros ajustables para facilitar pruebas deterministas.
    """

    def __init__(
        self,
        window_seconds: float = 30.0,
        _now: Callable[[], float] = _time_module.time,
    ) -> None:
        """Inicializa el limitador.

        Args:
            window_seconds: Duración de la ventana en segundos. Por defecto 30s.
            _now: Fuente de tiempo inyectable. Por defecto ``time.time``.
        """
        self._window = window_seconds
        self._now = _now
        # Clave: (agent_id, chat_id) | Valor: (window_start_ts, counter)
        self._state: dict[tuple[str, str], tuple[float, int]] = {}

    def check_and_increment(
        self,
        agent_id: str,
        chat_id: str,
        limit: int,
    ) -> BreachSignal | None:
        """Verifica y actualiza el contador para el par ``(agent_id, chat_id)``.

        Algoritmo de ventana fija:
        - Si la clave no existe → inicializa ventana en ``now``, contador = 1, retorna ``None``.
        - Si ``now - window_start < window_seconds`` → incrementa contador en ventana activa.
        - Si ``now - window_start >= window_seconds`` → resetea ventana a ``now``, contador = 1.
        - Después de actualizar: si ``counter > limit`` → retorna ``BreachSignal``; si no, ``None``.

        El umbral de breach es ``> limit`` (no ``>= limit``): exactamente ``limit``
        emisiones pasan por ventana. El primer intento que SUPERA el límite es
        rechazado.

        Args:
            agent_id: Identificador del agente emisor.
            chat_id: Identificador del chat (str, nunca int).
            limit: Número máximo de emisiones permitidas en la ventana.

        Returns:
            ``BreachSignal`` si se superó el límite; ``None`` si se puede emitir.
        """
        now = self._now()
        clave: tuple[str, str] = (agent_id, chat_id)

        entrada = self._state.get(clave)

        if entrada is None:
            # Primera emisión: abre ventana con contador 1.
            self._state[clave] = (now, 1)
            return None

        window_start, counter = entrada

        if now - window_start < self._window:
            # Dentro de la ventana activa: incrementa.
            counter += 1
            self._state[clave] = (window_start, counter)
        else:
            # Ventana expirada: resetea.
            window_start = now
            counter = 1
            self._state[clave] = (window_start, counter)

        if counter > limit:
            retry_in = max(0.0, self._window - (now - window_start))
            return BreachSignal(
                agent_id=agent_id,
                chat_id=chat_id,
                counter=counter,
                retry_in=retry_in,
            )

        return None

    @property
    def window_seconds(self) -> float:
        """Duración actual de la ventana en segundos."""
        return self._window

    def set_window(self, seconds: float) -> None:
        """Sobrescribe la duración de la ventana en runtime.

        El cambio aplica a partir del próximo ``check_and_increment``: las
        ventanas ya abiertas conservan su ``window_start`` y se reevalúan
        contra el nuevo umbral. Útil para overrides via comandos del bot
        (no se persiste en config).

        Args:
            seconds: Nueva duración de la ventana, debe ser > 0.

        Raises:
            ValueError: Si ``seconds`` es <= 0.
        """
        if seconds <= 0:
            raise ValueError(f"window_seconds debe ser > 0, recibido: {seconds}")
        self._window = float(seconds)

    def reset(self, agent_id: str, chat_id: str) -> None:
        """Elimina el estado de la ventana para el par ``(agent_id, chat_id)``.

        Útil en tests para limpiar el estado entre escenarios sin crear
        una nueva instancia del limitador.

        Args:
            agent_id: Identificador del agente.
            chat_id: Identificador del chat.
        """
        self._state.pop((agent_id, chat_id), None)
