"""Port de trazas de turno — el hilo de diagnóstico del modo debug.

El kernel emite eventos estructurados en los puntos clave del turno (arranque,
prompt ensamblado, cada respuesta del LLM, cada tool) y NO sabe qué se hace con
ellos: en producción normal el tracer es nulo; con ``app.debug`` (o ``--debug``)
el composition root inyecta uno que los escribe a disco.

Contrato: ``trace`` NUNCA lanza ni bloquea el turno. Una traza que rompe el
turno que intenta diagnosticar es peor que ninguna — la implementación captura
sus propios errores.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ITurnTracer(ABC):
    @abstractmethod
    def bind(self, **context: object) -> ITurnTracer:
        """Devuelve un tracer que agrega ``context`` a cada evento (agent_id, turn_id...)."""

    @abstractmethod
    def trace(self, event: str, **fields: object) -> None:
        """Registra un evento. Síncrono, best-effort, jamás lanza."""


class NullTurnTracer(ITurnTracer):
    """Tracer por defecto: no hace nada. Costo cero cuando el debug está apagado."""

    def bind(self, **context: object) -> ITurnTracer:
        return self

    def trace(self, event: str, **fields: object) -> None:
        return None
