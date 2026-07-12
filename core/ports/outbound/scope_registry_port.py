"""Puerto outbound para registrar qué scopes tienen un turno en curso.

Un *scope* es la tupla ``(agent_id, channel, chat_id)`` que identifica una
conversación dentro de un canal específico. Cuando ``RunAgentUseCase.execute()``
arranca para un scope, se marca como ocupado; cuando termina, se libera.

¿Para qué sirve? Los inbound adapters (Telegram privado, REST admin, REST por
agente) consultan este registro ANTES de invocar ``execute()``. Si el scope
ya está ocupado, en lugar de disparar un segundo ``execute()`` en paralelo,
persisten el mensaje del usuario en ``history.db`` vía
``RunAgentUseCase.record_user_message()`` y emiten un ACK efímero. El tool
loop del turno en curso lee ``history.db`` entre iteraciones y "drena" los
mensajes nuevos hacia ``working_messages``.

Decisión clave: **la fuente de verdad de los MENSAJES sigue siendo
``history.db``**. Este port solo trackea qué scopes están activos — es
coordinación pura, no almacenamiento. Esto evita duplicar lógica de
persistencia y reusa la pipeline ya probada del historial.

Implementaciones:
- ``InMemoryScopeRegistryAdapter`` — set + asyncio.Lock. Sin persistencia.
  Si el daemon reinicia, todos los scopes vuelven a estar libres
  (coherente con ``background-delegation``: uso doméstico Pi 5).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

# Type alias para legibilidad. Tres strings: (agent_id, channel, chat_id).
# Mantenerlo acá (en el port) evita crear un value object dedicado para algo
# que es realmente solo una clave compuesta.
Scope = tuple[str, str, str]


class IScopeRegistry(ABC):
    """Contrato para coordinar qué scopes tienen un turno de ejecución activo."""

    @abstractmethod
    async def try_mark_busy(self, scope: Scope) -> bool:
        """Marca el scope como ocupado de forma atómica.

        Retorna ``True`` si el scope estaba libre y se marcó ocupado
        (el caller "ganó" el slot). Retorna ``False`` si el scope ya
        estaba ocupado por otro caller (el caller debe encolar el
        mensaje en history y emitir ACK).

        La operación es atómica: dos corutinas que compiten por el mismo
        scope ven exactamente un ``True`` y un ``False`` (nunca dos
        ``True``).
        """
        ...

    @abstractmethod
    async def mark_idle(self, scope: Scope) -> None:
        """Libera el scope y descarta cualquier cancelación pendiente.

        Idempotente: si el scope no estaba marcado, es no-op silencioso.
        Pensado para llamarse desde ``finally`` aunque ``try_mark_busy``
        no se haya tomado (defensa en profundidad). Limpiar el flag de
        cancelación acá garantiza que un ``/stop`` que llegó tarde no
        envenene el próximo turno del scope.
        """
        ...

    @abstractmethod
    async def request_cancel(self, scope: Scope) -> bool:
        """Solicita cancelar el turno en curso del scope (kill-switch).

        Retorna ``True`` si el scope estaba ocupado y la solicitud quedó
        registrada; ``False`` si no había turno corriendo (no se registra
        nada — un flag sin turno envenenaría al próximo). El tool loop
        consulta el flag en sus checkpoints y aborta MECÁNICAMENTE: la
        cancelación no depende de que el LLM interprete nada — es la
        diferencia entre pedirle al chofer que frene y pisar el freno.
        """
        ...

    @abstractmethod
    async def is_cancel_requested(self, scope: Scope) -> bool:
        """``True`` si hay una cancelación pendiente para el scope. Solo lectura;
        el flag lo limpia ``mark_idle`` al cerrar el turno."""
        ...
