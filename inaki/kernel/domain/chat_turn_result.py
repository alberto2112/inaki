"""ChatTurnResult — DTO que devuelve ``IDaemonClient.chat_turn``.

Encapsula la respuesta final del agente junto con los bloques de texto
intermedios que emitió durante el turno (narración acompañando tool_calls).
El CLI los imprime en orden antes del ``reply`` para que el usuario vea el
progreso del turno tal cual sucedió en el daemon.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChatTurnResult:
    """Resultado completo de un turno de chat a través del daemon."""

    reply: str
    intermediates: list[str] = field(default_factory=list)
