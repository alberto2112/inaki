"""SQLiteBackgroundTaskStore — estado durable y transcript de las delegaciones en background.

Un fichero harness-global (``background_tasks.db``, tier compartido por todos los agentes;
las filas se separan por ``caller_agent_id``). Dos tablas:

  bg_tasks          — una fila por delegación: estado, presupuesto, scope, resultado completo
                      y fallo clasificado. ``id`` es AUTOINCREMENT sembrado en 999: el primer
                      id es ``bg-1000``, nunca se reutiliza ni tras podar todo.
  bg_task_messages  — el transcript del hijo, un INSERT por mensaje (``seq`` creciente) para
                      que un SIGKILL no pierda lo ya hecho. Cae en CASCADE con su task.

Decisiones de diseño (ver ``bg-delegation-transcript``):

- UNA conexión ``aiosqlite`` abierta en ``open()``, en autocommit, WAL y
  ``synchronous=NORMAL`` (la SD de la Pi sufre con un fsync por mensaje; con WAL+NORMAL un
  crash del PROCESO no pierde nada, solo un corte de energía puede perder el último commit).
  ``foreign_keys=ON`` es por conexión y obligatorio para el CASCADE. Como esa conexión la
  comparten los recorders de TODAS las tasks en curso, una vez que la cola arrancó NO se
  abren transacciones explícitas: se colarían los INSERT de otras tasks y un ROLLBACK los
  borraría en silencio (por eso ``snapshot_completo`` es de UPDATEs idempotentes por fila).
- "Tabla recién creada" se detecta por la AUSENCIA de ``bg_tasks`` en ``sqlite_master`` y no
  por ``COUNT(*)=0``: tras podar todo la tabla queda vacía y re-sembrar volvería los ids atrás.
- Cada mensaje se acota a ``MENSAJE_CAP_CHARS`` (cabeza 40 % / cola 60 % con marcador): la
  cola es donde viven los tracebacks y el final de un output. El resultado final de la task
  (``bg_tasks.result``) NUNCA se recorta, y el output completo no se guarda fuera de su fila.
- Retención en TASKS, no en filas: dos cotas simultáneas (edad y rango por caller). Las
  pausas ``awaiting_input`` caen solo por edad, fuera del ranking.

El store lanza si el disco falla (la cola decide cómo degradar), salvo el recorder de
``transcript()``, cuyo ``record`` nunca lanza (REQ-BGP-8).
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from inaki.agents.delegation.failure_report import tool_calls_sin_par
from inaki.agents.delegation.ports import ResultadoPoda, TranscriptNoReanudable
from inaki.kernel.domain.background_task import (
    BackgroundTask,
    DelegationFailure,
    TranscriptEntry,
)
from inaki.kernel.ports.delegation_transcript_port import IDelegationTranscript
from inaki.shared.message import Message, Role

logger = logging.getLogger(__name__)

# Tope de caracteres por mensaje persistido: cabeza 40 % + cola 60 % (el marcador va aparte).
MENSAJE_CAP_CHARS = 12450
_CABEZA_CHARS = MENSAJE_CAP_CHARS * 40 // 100  # 4980
_COLA_CHARS = MENSAJE_CAP_CHARS - _CABEZA_CHARS  # 7470

# Retención de tasks TERMINADAS por caller: se poda lo más viejo que RETENCION_DIAS O lo que
# quede fuera de las últimas RETENCION_ULTIMAS. Constantes del código, sin config (decisión
# de diseño: a la escala de una Pi doméstica un knob más es peso muerto).
RETENCION_DIAS = 14
RETENCION_ULTIMAS = 20

# El primer id de una DB nueva es bg-1000: distingue a simple vista un id durable de los
# ``bg-m{N}`` en memoria y deja los ids chicos libres.
_PRIMER_ID = 1000

_ID_DURABLE_RE = re.compile(r"bg-(\d+)")

_SCHEMA_TASKS = """
CREATE TABLE IF NOT EXISTS bg_tasks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    caller_agent_id     TEXT    NOT NULL,
    target_agent_id     TEXT    NOT NULL,
    prompt              TEXT    NOT NULL,
    system_prompt       TEXT,
    channel             TEXT    NOT NULL DEFAULT '',
    chat_id             TEXT    NOT NULL DEFAULT '',
    max_iterations      INTEGER NOT NULL,
    timeout_seconds     INTEGER NOT NULL,
    status              TEXT    NOT NULL,
    created_at          TEXT    NOT NULL,
    run_started_at      TEXT,
    finished_at         TEXT,
    iterations_used     INTEGER,
    result              TEXT,
    result_status       TEXT,
    result_parse_error  INTEGER NOT NULL DEFAULT 0,
    failure_kind        TEXT,
    failure_json        TEXT,
    delivery_error      TEXT,
    notified            INTEGER NOT NULL DEFAULT 1,
    workdir             TEXT,
    input_files         TEXT    NOT NULL DEFAULT '[]',
    transcript_incomplete INTEGER NOT NULL DEFAULT 0
)
"""
_SCHEMA_INDICES = (
    "CREATE INDEX IF NOT EXISTS ix_bg_tasks_caller_status ON bg_tasks(caller_agent_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_bg_tasks_aviso_pendiente ON bg_tasks(notified) "
    "WHERE notified = 0",
)
_SCHEMA_MESSAGES = """
CREATE TABLE IF NOT EXISTS bg_task_messages (
    task_id        INTEGER NOT NULL REFERENCES bg_tasks(id) ON DELETE CASCADE,
    seq            INTEGER NOT NULL,
    role           TEXT    NOT NULL,
    content        TEXT    NOT NULL,
    tool_calls     TEXT,
    tool_call_id   TEXT,
    original_chars INTEGER,
    created_at     TEXT    NOT NULL,
    PRIMARY KEY (task_id, seq)
) WITHOUT ROWID
"""

# Campos que ``update`` acepta: nombre del dominio → columna(s). Cualquier otro es un error.
# ``workdir`` e ``input_files`` NO están: se fijan al crear y son inmutables (un ``update``
# que los toque es un ``ValueError``, no un descarte silencioso).
_CAMPOS_ACTUALIZABLES = frozenset(
    {
        "status",
        "run_started_at",
        "finished_at",
        "iterations_used",
        "result",
        "result_status",
        "result_parse_error",
        "failure",
        "error",
        "notified",
        "transcript_incomplete",
    }
)

_COLUMNAS_TASK = (
    "id, caller_agent_id, target_agent_id, prompt, system_prompt, channel, chat_id, "
    "max_iterations, timeout_seconds, status, created_at, run_started_at, finished_at, "
    "iterations_used, result, result_status, result_parse_error, failure_json, "
    "delivery_error, notified, workdir, input_files, transcript_incomplete"
)

# Retención (REQ-BGP-7). Dos cotas SIMULTÁNEAS sobre las terminadas (incluida la
# ``interrupted`` ya avisada): fuera de las últimas N por caller O más vieja que el corte.
# Una ``interrupted`` sin avisar solo cae por edad. Nunca queued/running/delivering/
# delivery_failed. ``COALESCE`` cubre una terminal sin ``finished_at`` (defensa).
# ``awaiting_input`` (una pausa esperando respuesta) cae SOLO por edad —14 días desde la
# pausa, que es su ``finished_at``— y queda FUERA del ranking: si compitiera en las últimas
# N, 20 terminadas posteriores borrarían una pregunta de ayer. Su cláusula va en una línea
# propia: para meterla en el ranking basta moverla al ``status IN (...)`` de arriba.
# ``RETURNING`` (SQLite >= 3.35) devuelve el workdir de lo borrado en el mismo statement.
_SQL_PRUNE = """
DELETE FROM bg_tasks
WHERE id IN (
    SELECT id FROM (
        SELECT id,
               COALESCE(finished_at, created_at) AS fin,
               ROW_NUMBER() OVER (
                   PARTITION BY caller_agent_id
                   ORDER BY COALESCE(finished_at, created_at) DESC, id DESC
               ) AS rango
        FROM bg_tasks
        WHERE status IN ('delivered', 'cancelled', 'dismissed')
           OR (status = 'interrupted' AND notified = 1)
    )
    WHERE rango > :ultimas OR fin < :corte
)
OR (status = 'interrupted' AND notified = 0 AND COALESCE(finished_at, created_at) < :corte)
OR (status = 'awaiting_input' AND COALESCE(finished_at, created_at) < :corte)
RETURNING id, workdir
"""


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------


def _iso(valor: datetime) -> str:
    """ISO-8601 UTC de ancho fijo (microsegundos): las comparaciones de texto ordenan bien."""
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _fecha(texto: str | None) -> datetime | None:
    return datetime.fromisoformat(texto) if texto else None


def _rowid(task_id: str) -> int | None:
    """``bg-1000`` → 1000. ``None`` para ids no durables (``bg-m1``, basura)."""
    m = _ID_DURABLE_RE.fullmatch(task_id)
    return int(m.group(1)) if m else None


def _contenido_excede(texto: str) -> bool:
    """``True`` si el texto pasa el cap por mensaje (el escritor lo va a acotar)."""
    return len(texto) > MENSAJE_CAP_CHARS


def _tool_calls_exceden(tool_calls: list[dict] | None) -> bool:
    """``True`` si el JSON de ``tool_calls`` pasa el cap por mensaje."""
    if not tool_calls:
        return False
    return len(json.dumps(tool_calls, ensure_ascii=False)) > MENSAJE_CAP_CHARS


def fue_acotado(mensaje: Message) -> bool:
    """``True`` si el escritor del recorder acotó ``mensaje`` al persistirlo.

    Es el MISMO predicado que usan ``_acotar`` y ``_acotar_tool_calls``: el snapshot de una
    pausa reescribe exactamente lo que el escritor recortó, ni más ni menos. Mirar solo
    ``content`` no alcanza — un ``write_file`` enorme con ``content`` corto también se acota
    (en ``tool_calls``) y no deja ``original_chars``.
    """
    return _contenido_excede(mensaje.content) or _tool_calls_exceden(mensaje.tool_calls)


def _acotar(texto: str) -> tuple[str, int | None]:
    """Aplica el cap por mensaje. Devuelve ``(texto, original_chars | None)``.

    Hasta ``MENSAJE_CAP_CHARS`` se guarda íntegro (``original_chars`` = None). Más allá,
    cabeza 40 % + marcador con la cantidad omitida + cola 60 %.
    """
    if not _contenido_excede(texto):
        return texto, None
    omitidos = len(texto) - _CABEZA_CHARS - _COLA_CHARS
    marcador = f"\n[… {omitidos} characters omitted …]\n"
    return texto[:_CABEZA_CHARS] + marcador + texto[-_COLA_CHARS:], len(texto)


def _acotar_tool_calls(tool_calls: list[dict] | None) -> str | None:
    """Serializa ``tool_calls`` a JSON; si excede el cap, acota cada ``arguments``.

    ``arguments`` es un string JSON dentro del JSON: acotarlo como TEXTO mantiene el
    documento exterior válido (cortar el JSON serializado entero lo rompería). No muta
    el mensaje original — el ``trace`` en memoria conserva todo.
    """
    if not tool_calls:
        return None
    if not _tool_calls_exceden(tool_calls):
        return json.dumps(tool_calls, ensure_ascii=False)
    copia = copy.deepcopy(tool_calls)
    for tc in copia:
        funcion = tc.get("function")
        if not isinstance(funcion, dict) or "arguments" not in funcion:
            continue
        args = funcion["arguments"]
        texto = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
        funcion["arguments"] = _acotar(texto)[0]
    return json.dumps(copia, ensure_ascii=False)


def _lista_de_insumos(crudo: str | None, rowid: int) -> list[str]:
    """Decodifica la columna ``input_files`` (JSON de basenames); ilegible → lista vacía."""
    try:
        valor = json.loads(crudo or "[]")
    except ValueError:
        valor = None
    if isinstance(valor, list) and all(isinstance(v, str) for v in valor):
        return valor
    # Una fila ilegible no debe tumbar la lectura de todas las demás.
    logger.warning(
        "background-delegation bg-%s: input_files ilegible; se muestra sin insumos", rowid
    )
    return []


def _fila_a_task(fila: aiosqlite.Row) -> BackgroundTask:
    falla = None
    if fila["failure_json"]:
        try:
            falla = DelegationFailure.model_validate_json(fila["failure_json"])
        except ValueError:
            # Una fila ilegible no debe tumbar la lectura de todas las demás.
            logger.warning(
                "background-delegation bg-%s: failure_json ilegible; se muestra sin fallo "
                "clasificado",
                fila["id"],
            )
    return BackgroundTask(
        id=f"bg-{fila['id']}",
        caller_agent_id=fila["caller_agent_id"],
        target_agent_id=fila["target_agent_id"],
        prompt=fila["prompt"],
        system_prompt=fila["system_prompt"],
        channel=fila["channel"],
        chat_id=fila["chat_id"],
        max_iterations=fila["max_iterations"],
        timeout_seconds=fila["timeout_seconds"],
        started_at=datetime.fromisoformat(fila["created_at"]),
        status=fila["status"],
        result=fila["result"],
        error=fila["delivery_error"],
        run_started_at=_fecha(fila["run_started_at"]),
        finished_at=_fecha(fila["finished_at"]),
        iterations_used=fila["iterations_used"],
        result_status=fila["result_status"],
        result_parse_error=bool(fila["result_parse_error"]),
        failure=falla,
        notified=bool(fila["notified"]),
        workdir=fila["workdir"],
        input_files=_lista_de_insumos(fila["input_files"], fila["id"]),
        transcript_incomplete=bool(fila["transcript_incomplete"]),
    )


def _asignaciones(campos: dict[str, Any]) -> dict[str, Any]:
    """Traduce campos del dominio a ``{columna: valor SQL}``."""
    columnas: dict[str, Any] = {}
    for campo, valor in campos.items():
        if campo == "failure":
            columnas["failure_kind"] = valor.kind if valor is not None else None
            columnas["failure_json"] = valor.model_dump_json() if valor is not None else None
        elif campo == "error":
            columnas["delivery_error"] = valor
        elif campo in ("run_started_at", "finished_at"):
            columnas[campo] = _iso(valor) if valor is not None else None
        elif campo in ("result_parse_error", "notified", "transcript_incomplete"):
            columnas[campo] = int(bool(valor))
        else:
            columnas[campo] = valor
    return columnas


def _verificar_reanudable(filas: Sequence[aiosqlite.Row]) -> list[Message]:
    """Reconstruye el trace de ``filas`` o lanza ``TranscriptNoReanudable`` (REQ-RES-9).

    Un historial a medias no se reanuda: el provider lo rechazaría con 400 (tool call sin
    par) o el hijo seguiría sobre contenido recortado sin saberlo.
    """
    if not filas:
        raise TranscriptNoReanudable("el transcript está vacío")
    try:
        trace = [
            Message(
                role=Role(f["role"]),
                content=f["content"],
                tool_calls=json.loads(f["tool_calls"]) if f["tool_calls"] else None,
                tool_call_id=f["tool_call_id"],
            )
            for f in filas
        ]
    except ValueError as exc:
        raise TranscriptNoReanudable(f"hay una fila ilegible ({exc})") from exc
    if [f["seq"] for f in filas] != list(range(1, len(filas) + 1)):
        raise TranscriptNoReanudable("los seq no son contiguos de 1 a N (falta alguna fila)")
    if trace[0].role is not Role.USER:
        raise TranscriptNoReanudable("el primer mensaje no es del user")
    if any(f["original_chars"] is not None for f in filas):
        raise TranscriptNoReanudable("hay mensajes acotados (el snapshot no los completó)")
    if trace[-1].role is not Role.ASSISTANT or trace[-1].tool_calls:
        raise TranscriptNoReanudable("el último mensaje no es un assistant final sin tool_calls")
    sin_par = tool_calls_sin_par(trace)
    if sin_par.en_vuelo is not None:
        raise TranscriptNoReanudable(
            f"el tool call {sin_par.en_vuelo.tool_call_id} ({sin_par.en_vuelo.name}) "
            "no tiene resultado"
        )
    return trace


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


class SqliteDelegationTranscript:
    """``IDelegationTranscript`` respaldado por el store: un INSERT por mensaje.

    ``trace`` es el espejo en memoria (lo llena el use case one-shot y el tool loop; no
    depende del disco). ``record`` NUNCA lanza: ante el primer fallo loguea en ERROR la
    capacidad perdida, marca ``incompleto`` y deja de escribir (el transcript queda como un
    prefijo consistente, sin huecos). La escritura va protegida con ``asyncio.shield`` para
    que un timeout que cancela al hijo justo en el ``await`` no pierda el último resultado.
    """

    def __init__(
        self,
        store: SQLiteBackgroundTaskStore,
        task_id: str,
        rowid: int,
        *,
        trace: list[Message] | None = None,
        seq: int = 0,
    ) -> None:
        self._store = store
        self._task_id = task_id
        self._rowid = rowid
        # Un recorder REABIERTO (resume) arranca con el trace ya persistido y ``seq`` en el
        # último valor: el próximo ``record`` escribe ``seq + 1``, sin re-INSERT.
        self._trace: list[Message] = trace if trace is not None else []
        self._seq = seq
        self._roto = False

    @property
    def trace(self) -> list[Message]:
        return self._trace

    @property
    def incompleto(self) -> bool:
        """``True`` si alguna escritura falló: el transcript durable está cortado."""
        return self._roto

    async def record(self, message: Message) -> None:
        if self._roto:
            logger.debug("%s: transcript ya no se persiste; se omite el paso", self._task_id)
            return
        self._seq += 1
        escritura = asyncio.ensure_future(self._escribir(self._seq, message))
        self._store._registrar_pendiente(escritura)
        await asyncio.shield(escritura)

    async def _escribir(self, seq: int, message: Message) -> None:
        try:
            contenido, original = _acotar(message.content)
            db = self._store._conn()
            await db.execute(
                "INSERT INTO bg_task_messages "
                "(task_id, seq, role, content, tool_calls, tool_call_id, original_chars, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._rowid,
                    seq,
                    message.role.value,
                    contenido,
                    _acotar_tool_calls(message.tool_calls),
                    message.tool_call_id,
                    original,
                    _iso(datetime.now(timezone.utc)),
                ),
            )
        except Exception as exc:  # noqa: BLE001 — el contrato es no lanzar jamás
            self._roto = True
            logger.error(
                "background task transcript/durable state unavailable: %s dejó de persistir "
                "su transcript en el paso %d (%s: %s); inspect lo mostrará solo hasta el "
                "paso %d y un reinicio no recuperará el resto",
                self._task_id,
                seq,
                type(exc).__name__,
                exc,
                seq - 1,
            )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class SQLiteBackgroundTaskStore:
    """Implementa ``IBackgroundTaskStore`` sobre SQLite (ver docstring del módulo)."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._db: aiosqlite.Connection | None = None
        # Escrituras del recorder en vuelo: ``close`` las espera antes de cerrar.
        self._pendientes: set[asyncio.Future[None]] = set()

    # -- ciclo de vida -------------------------------------------------------

    async def open(self) -> None:
        if self._db is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None → autocommit: cada INSERT del recorder queda confirmado ya.
        db = await aiosqlite.connect(self._path, isolation_level=None)
        try:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.execute("PRAGMA foreign_keys=ON")
            await self._ensure_schema(db)
        except BaseException:
            await db.close()
            raise
        self._db = db

    async def close(self) -> None:
        if self._db is None:
            return
        if self._pendientes:
            await asyncio.gather(*self._pendientes, return_exceptions=True)
        db, self._db = self._db, None
        await db.close()

    @staticmethod
    async def _ensure_schema(db: aiosqlite.Connection) -> None:
        """Crea tablas e índices; siembra el contador solo si la tabla es NUEVA.

        Todo en una transacción ``IMMEDIATE``: dos procesos que abren a la vez se
        serializan, y la detección ("¿existía ``bg_tasks``?") y la siembra no se parten.
        """
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'bg_tasks'"
            ) as cur:
                nueva = await cur.fetchone() is None
            await db.execute(_SCHEMA_TASKS)
            for indice in _SCHEMA_INDICES:
                await db.execute(indice)
            await db.execute(_SCHEMA_MESSAGES)
            if nueva:
                await db.execute(
                    "INSERT INTO sqlite_sequence (name, seq) VALUES ('bg_tasks', ?)",
                    (_PRIMER_ID - 1,),
                )
            await db.execute("COMMIT")
        except BaseException:
            with suppress(Exception):
                await db.execute("ROLLBACK")
            raise

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SQLiteBackgroundTaskStore: el store no está abierto (falta open())")
        return self._db

    def _registrar_pendiente(self, escritura: asyncio.Future[None]) -> None:
        self._pendientes.add(escritura)
        escritura.add_done_callback(self._pendientes.discard)

    # -- escritura ------------------------------------------------------------

    async def create(self, task: BackgroundTask) -> str:
        cur = await self._conn().execute(
            "INSERT INTO bg_tasks (caller_agent_id, target_agent_id, prompt, system_prompt, "
            "channel, chat_id, max_iterations, timeout_seconds, status, created_at, "
            "workdir, input_files) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task.caller_agent_id,
                task.target_agent_id,
                task.prompt,
                task.system_prompt,
                task.channel,
                task.chat_id,
                task.max_iterations,
                task.timeout_seconds,
                task.status,
                _iso(task.started_at),
                task.workdir,
                json.dumps(task.input_files, ensure_ascii=False),
            ),
        )
        return f"bg-{cur.lastrowid}"

    async def update(self, task_id: str, **campos: Any) -> None:
        desconocidos = set(campos) - _CAMPOS_ACTUALIZABLES
        if desconocidos:
            raise ValueError(
                f"update de {task_id}: campo(s) no actualizable(s): {sorted(desconocidos)}"
            )
        rowid = _rowid(task_id)
        if rowid is None:
            raise ValueError(f"update: {task_id!r} no es un id durable")
        if not campos:
            return
        columnas = _asignaciones(campos)
        sets = ", ".join(f"{col} = ?" for col in columnas)
        await self._conn().execute(
            f"UPDATE bg_tasks SET {sets} WHERE id = ?",  # noqa: S608 — columnas de lista blanca
            (*columnas.values(), rowid),
        )

    async def save_outcome(self, task: BackgroundTask) -> None:
        await self.update(
            task.id,
            status=task.status,
            result=task.result,
            result_status=task.result_status,
            result_parse_error=task.result_parse_error,
            failure=task.failure,
            iterations_used=task.iterations_used,
            finished_at=task.finished_at,
        )

    def transcript(self, task_id: str) -> IDelegationTranscript:
        rowid = _rowid(task_id)
        if rowid is None:
            raise ValueError(f"transcript: {task_id!r} no es un id durable")
        return SqliteDelegationTranscript(self, task_id, rowid)

    async def reabrir_transcript(self, task_id: str) -> IDelegationTranscript:
        rowid = _rowid(task_id)
        if rowid is None:
            raise ValueError(f"reabrir_transcript: {task_id!r} no es un id durable")
        async with self._conn().execute(
            "SELECT seq, role, content, tool_calls, tool_call_id, original_chars "
            "FROM bg_task_messages WHERE task_id = ? ORDER BY seq",
            (rowid,),
        ) as cur:
            filas = list(await cur.fetchall())
        trace = _verificar_reanudable(filas)
        return SqliteDelegationTranscript(self, task_id, rowid, trace=trace, seq=len(trace))

    async def snapshot_completo(
        self, task_id: str, trace: Sequence[Message], *, desde_seq: int
    ) -> None:
        rowid = _rowid(task_id)
        if rowid is None:
            raise ValueError(f"snapshot_completo: {task_id!r} no es un id durable")
        db = self._conn()
        async with db.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM bg_task_messages WHERE task_id = ?", (rowid,)
        ) as cur:
            persistidos = (await cur.fetchone())[0]  # type: ignore[index]
        if persistidos != len(trace):
            raise ValueError(
                f"snapshot_completo {task_id}: el trace en memoria tiene {len(trace)} mensajes "
                f"y el disco {persistidos}; no se reescribe nada sobre un transcript desalineado"
            )
        # Sin BEGIN/COMMIT a propósito: la conexión es compartida y está en autocommit, y una
        # transacción abierta se tragaría los INSERT de los recorders de OTRAS tasks (un
        # ROLLBACK los borraría en silencio). Cada UPDATE es idempotente por sí solo; un fallo
        # a mitad deja filas ya completas (inocuo) y el caller lo trata como "no reanudable".
        for seq in range(desde_seq + 1, len(trace) + 1):
            mensaje = trace[seq - 1]
            if not fue_acotado(mensaje):
                continue
            await db.execute(
                "UPDATE bg_task_messages SET content = ?, tool_calls = ?, original_chars = NULL "
                "WHERE task_id = ? AND seq = ?",
                (
                    mensaje.content,
                    json.dumps(mensaje.tool_calls, ensure_ascii=False)
                    if mensaje.tool_calls
                    else None,
                    rowid,
                    seq,
                ),
            )

    async def mark_notified(self, task_ids: Sequence[str]) -> None:
        rowids = [r for r in (_rowid(t) for t in task_ids) if r is not None]
        if not rowids:
            return
        marcas = ", ".join("?" for _ in rowids)
        await self._conn().execute(
            f"UPDATE bg_tasks SET notified = 1 WHERE id IN ({marcas})",  # noqa: S608
            rowids,
        )

    async def prune(self, now: datetime) -> ResultadoPoda:
        corte = _iso(now - timedelta(days=RETENCION_DIAS))
        async with self._conn().execute(
            _SQL_PRUNE, {"ultimas": RETENCION_ULTIMAS, "corte": corte}
        ) as cur:
            # Hay que consumir todo el RETURNING: el DELETE no termina hasta entonces.
            filas = sorted(await cur.fetchall(), key=lambda f: f["id"])
        return ResultadoPoda(
            borradas=len(filas),
            workdirs=[(f"bg-{f['id']}", f["workdir"]) for f in filas if f["workdir"]],
        )

    async def workdirs_referenciados(self) -> set[str]:
        async with self._conn().execute(
            "SELECT workdir FROM bg_tasks WHERE workdir IS NOT NULL"
        ) as cur:
            return {f["workdir"] for f in await cur.fetchall()}

    # -- lectura --------------------------------------------------------------

    async def get(self, task_id: str, caller_agent_id: str) -> BackgroundTask | None:
        rowid = _rowid(task_id)
        if rowid is None:
            return None
        async with self._conn().execute(
            f"SELECT {_COLUMNAS_TASK} FROM bg_tasks WHERE id = ? AND caller_agent_id = ?",  # noqa: S608
            (rowid, caller_agent_id),
        ) as cur:
            fila = await cur.fetchone()
        return _fila_a_task(fila) if fila is not None else None

    async def list_by_status(self, statuses: Sequence[str]) -> list[BackgroundTask]:
        if not statuses:
            return []
        marcas = ", ".join("?" for _ in statuses)
        async with self._conn().execute(
            f"SELECT {_COLUMNAS_TASK} FROM bg_tasks "  # noqa: S608
            f"WHERE status IN ({marcas}) ORDER BY id",
            tuple(statuses),
        ) as cur:
            return [_fila_a_task(f) for f in await cur.fetchall()]

    async def pending_notices(self) -> list[BackgroundTask]:
        async with self._conn().execute(
            f"SELECT {_COLUMNAS_TASK} FROM bg_tasks "  # noqa: S608
            "WHERE status = 'interrupted' AND notified = 0 ORDER BY id"
        ) as cur:
            return [_fila_a_task(f) for f in await cur.fetchall()]

    async def messages(self, task_id: str, *, last_n: int) -> tuple[list[TranscriptEntry], int]:
        rowid = _rowid(task_id)
        if rowid is None:
            return [], 0
        db = self._conn()
        async with db.execute(
            "SELECT COUNT(*) FROM bg_task_messages WHERE task_id = ?", (rowid,)
        ) as cur:
            total = (await cur.fetchone())[0]  # type: ignore[index]
        if last_n <= 0 or total == 0:
            return [], total
        async with db.execute(
            "SELECT seq, role, content, tool_calls, tool_call_id, original_chars, created_at "
            "FROM bg_task_messages WHERE task_id = ? ORDER BY seq DESC LIMIT ?",
            (rowid, last_n),
        ) as cur:
            filas = list(await cur.fetchall())
        pasos = [
            TranscriptEntry(
                seq=f["seq"],
                role=Role(f["role"]),
                content=f["content"],
                tool_calls=json.loads(f["tool_calls"]) if f["tool_calls"] else None,
                tool_call_id=f["tool_call_id"],
                original_chars=f["original_chars"],
                created_at=datetime.fromisoformat(f["created_at"]),
            )
            for f in reversed(filas)
        ]
        return pasos, total
