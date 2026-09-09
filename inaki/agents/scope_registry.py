"""Implementación en memoria de ``IScopeRegistry``.

Usa un ``set`` protegido por un ``asyncio.Lock`` global. No persiste estado:
si el daemon reinicia, todos los scopes vuelven a estar libres. Trade-off
coherente con ``background-delegation`` — uso doméstico Pi 5, sin recovery
on startup, sin retries.

¿Por qué un solo lock global y no uno por scope?
- ``LLMDispatcherAdapter`` mantiene un dict de locks-por-scope que crece sin
  bound. Funciona pero suma complejidad.
- En este registry, la operación crítica (``try_mark_busy``) es O(1):
  un check ``in set`` + un ``add``. El contention real de un solo lock
  global es despreciable para el uso esperado (pocos agentes, pocos
  chats simultáneos en un Pi 5 doméstico).
- Simplicidad > optimización prematura.
"""

from __future__ import annotations

import asyncio
import logging

from core.ports.outbound.scope_registry_port import IScopeRegistry, Scope

logger = logging.getLogger(__name__)


class InMemoryScopeRegistryAdapter(IScopeRegistry):
    """Set en memoria con un asyncio.Lock para serializar transiciones."""

    def __init__(self) -> None:
        # Scopes actualmente ocupados (con execute() en curso).
        self._busy: set[Scope] = set()
        # Scopes con cancelación pendiente (kill-switch /stop). Siempre un
        # subconjunto de _busy: request_cancel exige turno en curso y
        # mark_idle limpia ambos sets.
        self._cancel: set[Scope] = set()
        # Lock único: protege las transiciones de ``_busy``. Para uso doméstico
        # con baja concurrencia, no compensa la complejidad de un lock-por-scope.
        self._lock = asyncio.Lock()

    async def try_mark_busy(self, scope: Scope) -> bool:
        async with self._lock:
            if scope in self._busy:
                logger.info("[scope-registry] busy scope=%s", scope)
                return False
            self._busy.add(scope)
            logger.info("[scope-registry] acquired scope=%s", scope)
            return True

    async def mark_idle(self, scope: Scope) -> None:
        async with self._lock:
            # discard NO lanza si el scope no estaba — comportamiento
            # idempotente requerido por el contrato del port.
            had_it = scope in self._busy
            self._busy.discard(scope)
            # Un turno que cierra descarta cualquier cancelación pendiente:
            # un /stop tardío no debe envenenar el próximo turno del scope.
            self._cancel.discard(scope)
            if had_it:
                logger.info("[scope-registry] released scope=%s", scope)
            else:
                # No es un error, pero loguear ayuda a debuggear casos donde
                # ``mark_idle`` se llama sin un ``try_mark_busy`` previo.
                logger.debug(
                    "[scope-registry] mark_idle no-op (was not busy) scope=%s",
                    scope,
                )

    async def request_cancel(self, scope: Scope) -> bool:
        async with self._lock:
            if scope not in self._busy:
                logger.info("[scope-registry] cancel rechazado (scope libre) scope=%s", scope)
                return False
            self._cancel.add(scope)
            logger.info("[scope-registry] cancel solicitado scope=%s", scope)
            return True

    async def is_cancel_requested(self, scope: Scope) -> bool:
        async with self._lock:
            return scope in self._cancel
