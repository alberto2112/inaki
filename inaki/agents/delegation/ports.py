"""Port del store durable de delegaciones en background.

Es un colaborador PRIVADO de ``BackgroundDelegationQueueAdapter`` y por eso vive en el
módulo ``agents`` y no en ``kernel/ports/``: el kernel declara los ports que CONSUME, y
el store solo lo consume la cola. Lo que el kernel sí consume (el recorder que anota el
transcript del hijo) es ``IDelegationTranscript``, en ``kernel/ports/``.

El store es la fuente de verdad DURABLE de toda task; el dict en memoria de la cola es
una proyección de las vivas (ver el diseño de ``bg-delegation-transcript``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from inaki.kernel.domain.background_task import BackgroundTask, TranscriptEntry
from inaki.kernel.ports.delegation_transcript_port import IDelegationTranscript


@dataclass(frozen=True)
class ResultadoPoda:
    """Lo que devuelve ``prune``: cuántas tasks borró y los workdirs de esas filas.

    ``workdirs`` son pares ``(task_id, ruta absoluta)`` de las filas BORRADAS que tenían
    workdir. La fila ya no existe cuando el caller recibe la ruta: borra el directorio
    DESPUÉS (un crash entre ambos lo levanta el barrido de arranque).
    """

    borradas: int
    workdirs: list[tuple[str, str]] = field(default_factory=list)


class IBackgroundTaskStore(Protocol):
    """Persistencia de tasks y de su transcript. Todas las operaciones son async.

    Contrato de errores: ``open``, ``create``, ``update``, ``save_outcome`` y las
    lecturas LANZAN si el disco falla — la cola decide cómo degradar (ruidosamente,
    nunca matando la delegación). La excepción es el recorder que devuelve
    ``transcript``: su ``record`` nunca lanza.
    """

    async def open(self) -> None:
        """Abre la conexión y crea el esquema si hace falta. Idempotente."""
        ...

    async def close(self) -> None:
        """Espera las escrituras en vuelo y cierra la conexión. Idempotente."""
        ...

    async def create(self, task: BackgroundTask) -> str:
        """Inserta la task y devuelve su id durable (``bg-{rowid}``).

        El ``id`` de la task recibida se IGNORA: el id lo asigna el store. Quien llama
        debe tomar el devuelto.
        """
        ...

    async def update(self, task_id: str, **campos: Any) -> None:
        """Actualiza campos de la task (``status``, ``finished_at``, ``failure``...).

        Idempotente: aplicar el mismo update dos veces deja el mismo estado. Un campo
        desconocido es un ``ValueError`` (nunca se descarta en silencio).
        """
        ...

    async def save_outcome(self, task: BackgroundTask) -> None:
        """Persiste el desenlace del hijo (resultado y/o fallo) junto con ``status``.

        Se llama ANTES de entregar al padre (REQ-BGP-4): si el dispatch o el daemon
        mueren, el resultado ya está a salvo.
        """
        ...

    def transcript(self, task_id: str) -> IDelegationTranscript:
        """Recorder SQLite para la task: un INSERT por mensaje, en orden, sin lanzar."""
        ...

    async def list_by_status(self, statuses: Sequence[str]) -> list[BackgroundTask]:
        """Tasks cuyo estado está en ``statuses``, ordenadas por id."""
        ...

    async def messages(self, task_id: str, *, last_n: int) -> tuple[list[TranscriptEntry], int]:
        """Últimos ``last_n`` pasos del transcript (en orden) y el total de pasos."""
        ...

    async def get(self, task_id: str, caller_agent_id: str) -> BackgroundTask | None:
        """La task, o ``None`` si no existe, se podó, es de OTRO caller o el id no es durable."""
        ...

    async def pending_notices(self) -> list[BackgroundTask]:
        """Tasks ``interrupted`` cuyo aviso al padre todavía no se entregó (``notified=0``)."""
        ...

    async def mark_notified(self, task_ids: Sequence[str]) -> None:
        """Marca esas tasks como ya avisadas. Ids no durables se ignoran."""
        ...

    async def prune(self, now: datetime) -> ResultadoPoda:
        """Aplica la retención (sus mensajes caen en cascada).

        Devuelve cuántas tasks borró y los workdirs de las borradas, obtenidos en el
        MISMO statement que borra la fila (sin carrera con otro escritor). Todo camino
        futuro que borre filas MUST devolver también sus workdirs.
        """
        ...

    async def workdirs_referenciados(self) -> set[str]:
        """Rutas absolutas de workdir que alguna fila referencia (para el barrido de huérfanos)."""
        ...
