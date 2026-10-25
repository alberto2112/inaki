"""Tests de la cola de delegaciones CON store durable (SQLite real en ``tmp_path``).

Cubre la integración cola ↔ store de ``bg-delegation-transcript``: ids durables, degradación
ruidosa cuando el disco falla (REQ-BGP-8, nivel ERROR por la decisión R1), persistencia del
resultado ANTES del dispatch (REQ-BGP-4), fallos honestos (REQ-BGP-5/6), cancelación y barrido
de arranque (REQ-BGD-8..10), workdirs (REQ-BGW-*) y aviso coalescido (REQ-BGD-11).

El store es el REAL; solo se rompe a propósito (``StoreRoto``) para probar la degradación.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from inaki.agents.delegation.background_queue import BackgroundDelegationQueueAdapter
from inaki.agents.delegation.result_contract import RESULT_FORMAT_FOOTER
from inaki.agents.delegation.sqlite_task_store import SQLiteBackgroundTaskStore
from inaki.agents.delegation.workdirs import GestorDeWorkdirs
from inaki.kernel.domain.background_task import BackgroundTask, EnqueuedTask, InputFilesRejected
from inaki.kernel.domain.agent_settings import OneShotSettings
from inaki.kernel.domain.llm_response import LLMResponse
from inaki.kernel.run_agent_one_shot import RunAgentOneShotUseCase
from inaki.kernel.ports.delegation_transcript_port import NullDelegationTranscript
from inaki.shared.errors import AgentNotFoundError, ToolLoopMaxIterationsError
from inaki.shared.message import Message, Role

_LOGGER = "inaki.agents.delegation.background_queue"
_CAPACIDAD = "background task transcript/durable state unavailable"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class StoreRoto(SQLiteBackgroundTaskStore):
    """Store real al que se le puede romper una operación a la vez (disco lleno, I/O)."""

    def __init__(self, path: Path, *, fallar: tuple[str, ...] = ()) -> None:
        super().__init__(path)
        self.fallar = set(fallar)
        self.llamadas: Counter[str] = Counter()

    def _probar(self, operacion: str) -> None:
        self.llamadas[operacion] += 1
        if operacion in self.fallar:
            raise sqlite3.OperationalError(f"disk I/O error ({operacion})")

    async def open(self) -> None:
        self._probar("open")
        await super().open()

    async def create(self, task):
        self._probar("create")
        return await super().create(task)

    async def update(self, task_id, **campos):
        self._probar("update")
        return await super().update(task_id, **campos)

    async def save_outcome(self, task):
        self._probar("save_outcome")
        return await super().save_outcome(task)

    async def list_by_status(self, statuses):
        self._probar("list_by_status")
        return await super().list_by_status(statuses)

    async def prune(self, now):
        self._probar("prune")
        return await super().prune(now)

    async def workdirs_referenciados(self):
        self._probar("workdirs_referenciados")
        return await super().workdirs_referenciados()

    async def pending_notices(self):
        self._probar("pending_notices")
        return await super().pending_notices()

    async def mark_notified(self, task_ids):
        self._probar("mark_notified")
        return await super().mark_notified(task_ids)


def _one_shot(valor: str = "ok", *, system_prompt: str = "Sos el hijo.") -> MagicMock:
    mock = MagicMock()
    mock.system_prompt = system_prompt
    mock.execute = AsyncMock(return_value=valor)
    return mock


def _armar(
    *,
    store=None,
    one_shot_for: dict | None = None,
    dispatcher: MagicMock | None = None,
    max_concurrent: int = 3,
    workdirs: GestorDeWorkdirs | None = None,
) -> tuple[BackgroundDelegationQueueAdapter, MagicMock]:
    dispatcher = dispatcher or MagicMock()
    if not isinstance(dispatcher.dispatch, AsyncMock):
        dispatcher.dispatch = AsyncMock(return_value="")
    pool = one_shot_for or {}
    adapter = BackgroundDelegationQueueAdapter(
        dispatcher=dispatcher,
        one_shot_resolver=lambda caller, target: pool.get(target),
        max_concurrent=max_concurrent,
        store=store,
        workdirs=workdirs,
    )
    return adapter, dispatcher


async def _encolar_completo(
    adapter,
    *,
    caller: str = "inaki",
    target: str = "r",
    prompt: str = "investigá X",
    max_iterations: int = 5,
    timeout_seconds: int = 30,
    **extra,
) -> EnqueuedTask:
    return await adapter.enqueue(
        caller_agent_id=caller,
        target_agent_id=target,
        prompt=prompt,
        system_prompt=None,
        channel="telegram",
        chat_id="42",
        max_iterations=max_iterations,
        timeout_seconds=timeout_seconds,
        **extra,
    )


async def _encolar(adapter, **kw) -> str:
    """Encola y devuelve solo el ``task_id`` (lo que usa la mayoría de los tests)."""
    return (await _encolar_completo(adapter, **kw)).task_id


def _errores(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno == logging.ERROR]


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    s = SQLiteBackgroundTaskStore(tmp_path / "background_tasks.db")
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# 4.1 — store opcional, ids, _transicionar, degradación ruidosa (REQ-BGP-1, REQ-BGP-8)
# ---------------------------------------------------------------------------


class TestIdsYPersistenciaBasica:
    async def test_con_store_los_ids_son_durables_desde_bg_1000(self, store) -> None:
        adapter, _ = _armar(store=store)
        await adapter.start()

        primero = await _encolar(adapter)
        segundo = await _encolar(adapter)
        await adapter.stop()

        assert (primero, segundo) == ("bg-1000", "bg-1001")

    async def test_la_fila_nace_queued_con_el_scope_y_el_presupuesto(self, store) -> None:
        adapter, _ = _armar(store=store)
        await adapter.start()

        task_id = await _encolar(adapter, prompt="investigá el saldo")
        fila = await store.get(task_id, "inaki")
        await adapter.stop()

        assert fila is not None
        assert (fila.status, fila.prompt) == ("queued", "investigá el saldo")
        assert (fila.channel, fila.chat_id) == ("telegram", "42")
        assert (fila.max_iterations, fila.timeout_seconds) == (5, 30)

    async def test_sin_store_los_ids_son_bg_m_y_no_colisionan_con_los_durables(self) -> None:
        adapter, _ = _armar(store=None)

        ids = [await _encolar(adapter), await _encolar(adapter), await _encolar(adapter)]

        assert ids == ["bg-m1", "bg-m2", "bg-m3"]

    async def test_cada_transicion_llega_a_la_fila(self, store) -> None:
        """running y luego el estado terminal quedan en el store, no solo en el dict."""
        visto: dict = {}
        hijo = _one_shot()

        async def espiar(**_kw):
            visto["fila"] = await store.get(visto["id"], "inaki")
            return "ok"

        hijo.execute = AsyncMock(side_effect=espiar)
        adapter, _ = _armar(store=store, one_shot_for={"r": hijo})
        await adapter.start()

        visto["id"] = await _encolar(adapter)
        await asyncio.sleep(0.1)
        final = await store.get(visto["id"], "inaki")
        await adapter.stop()

        assert visto["fila"].status == "running"
        assert visto["fila"].run_started_at is not None
        assert final.status == "delivered"


class TestDegradacionRuidosa:
    """REQ-BGP-8 (R1): el disco es una dependencia externa; si falla, la delegación sigue,
    pero el log (ERROR) nombra la capacidad que se perdió."""

    async def test_open_que_falla_loguea_error_y_la_delegacion_sigue_en_memoria(
        self, tmp_path: Path, caplog
    ) -> None:
        caplog.set_level(logging.ERROR, logger=_LOGGER)
        roto = StoreRoto(tmp_path / "bg.db", fallar=("open",))
        adapter, dispatcher = _armar(store=roto, one_shot_for={"r": _one_shot()})

        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.1)
        await adapter.stop()

        assert task_id == "bg-m1"
        dispatcher.dispatch.assert_awaited_once()
        errores = _errores(caplog)
        assert len(errores) == 1
        assert _CAPACIDAD in errores[0].getMessage()
        assert "EN MEMORIA" in errores[0].getMessage()

    async def test_create_que_falla_usa_un_id_en_memoria_y_entrega_igual(
        self, tmp_path: Path, caplog
    ) -> None:
        caplog.set_level(logging.ERROR, logger=_LOGGER)
        roto = StoreRoto(tmp_path / "bg.db", fallar=("create",))
        adapter, dispatcher = _armar(store=roto, one_shot_for={"r": _one_shot()})
        await adapter.start()

        task_id = await _encolar(adapter)
        await asyncio.sleep(0.1)
        await adapter.stop()

        assert task_id == "bg-m1"
        assert f"[{task_id}]" in dispatcher.dispatch.await_args.kwargs["prompt"]
        errores = _errores(caplog)
        assert len(errores) == 1
        assert _CAPACIDAD in errores[0].getMessage()
        assert task_id in errores[0].getMessage()
        # Una task sin fila no intenta actualizar filas que no existen.
        assert roto.llamadas["update"] == 0

    async def test_update_que_falla_loguea_error_UNA_vez_por_task_y_entrega_igual(
        self, tmp_path: Path, caplog
    ) -> None:
        """Varias transiciones (running, delivering, delivered) → un solo ERROR para la task."""
        caplog.set_level(logging.ERROR, logger=_LOGGER)
        roto = StoreRoto(tmp_path / "bg.db", fallar=("update", "save_outcome"))
        adapter, dispatcher = _armar(store=roto, one_shot_for={"r": _one_shot()})
        await adapter.start()

        task_id = await _encolar(adapter)
        await asyncio.sleep(0.1)
        await adapter.stop()

        dispatcher.dispatch.assert_awaited_once()  # el resultado llegó desde memoria
        errores = _errores(caplog)
        assert len(errores) == 1
        assert _CAPACIDAD in errores[0].getMessage()
        assert task_id in errores[0].getMessage()
        assert roto.llamadas["update"] == 1  # tras el primer fallo se deja de intentar

    async def test_dos_tasks_rotas_loguean_cada_una_su_error(self, tmp_path: Path, caplog) -> None:
        """Triangulación: 'una vez por task' no es 'una vez en total'."""
        caplog.set_level(logging.ERROR, logger=_LOGGER)
        roto = StoreRoto(tmp_path / "bg.db", fallar=("update", "save_outcome"))
        adapter, _ = _armar(store=roto, one_shot_for={"r": _one_shot()})
        await adapter.start()

        a = await _encolar(adapter)
        b = await _encolar(adapter)
        await asyncio.sleep(0.15)
        await adapter.stop()

        mensajes = " ".join(r.getMessage() for r in _errores(caplog))
        assert len(_errores(caplog)) == 2
        assert a in mensajes and b in mensajes

    async def test_el_resultado_se_entrega_aunque_no_se_pueda_persistir_antes_del_dispatch(
        self, tmp_path: Path
    ) -> None:
        """REQ-BGP-8 S3: falla al persistir el resultado → igual se dispatcha desde memoria."""
        roto = StoreRoto(tmp_path / "bg.db", fallar=("update", "save_outcome"))
        adapter, dispatcher = _armar(
            store=roto, one_shot_for={"r": _one_shot('```json\n{"status":"success"}\n```')}
        )
        await adapter.start()

        task_id = await _encolar(adapter)
        await asyncio.sleep(0.1)
        await adapter.stop()

        assert dispatcher.dispatch.await_args.kwargs["prompt"].startswith(f"[{task_id}]")
        assert task_id not in adapter._tasks  # y se purgó como siempre


# ---------------------------------------------------------------------------
# 4.8 — workdir al ENCOLAR (REQ-BGW-1/2/3/5)
# ---------------------------------------------------------------------------


@pytest.fixture
def raiz(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def _crear(gestor: GestorDeWorkdirs, caller: str = "inaki") -> Path:
    ruta = gestor.crear(caller)
    assert ruta is not None
    return ruta


def _gestor(raiz: Path, *, con_flag: bool = True) -> GestorDeWorkdirs:
    return GestorDeWorkdirs(workspaces={"inaki": raiz}, con_workdir=["inaki"] if con_flag else [])


class TestWorkdirAlEncolar:
    async def test_con_el_flag_se_crea_el_directorio_y_se_devuelve_la_ruta(
        self, store, raiz: Path
    ) -> None:
        adapter, _ = _armar(store=store, workdirs=_gestor(raiz))
        await adapter.start()

        encolada = await _encolar_completo(adapter)
        await adapter.stop()

        assert encolada.workdir is not None
        creado = Path(encolada.workdir)
        assert creado.is_dir()
        assert creado.parent == raiz.resolve() / "dwss"
        assert len(creado.name) == 8
        assert encolada.workdir_unavailable is False
        assert encolada.input_files == []

    async def test_la_ruta_absoluta_queda_en_la_fila_y_sobrevive_al_reinicio(
        self, store, raiz: Path
    ) -> None:
        adapter, _ = _armar(store=store, workdirs=_gestor(raiz))
        await adapter.start()
        encolada = await _encolar_completo(adapter)
        await adapter.stop()

        await store.open()  # "reinicio": se relee la fila desde el disco
        fila = await store.get(encolada.task_id, "inaki")

        assert fila is not None
        assert fila.workdir == encolada.workdir

    async def test_dos_delegaciones_reciben_directorios_distintos(self, store, raiz: Path) -> None:
        adapter, _ = _armar(store=store, workdirs=_gestor(raiz))
        await adapter.start()

        a = await _encolar_completo(adapter)
        b = await _encolar_completo(adapter)
        await adapter.stop()

        assert a.workdir != b.workdir
        assert Path(a.workdir).is_dir() and Path(b.workdir).is_dir()  # type: ignore[arg-type]

    async def test_sin_el_flag_no_se_crea_nada(self, store, raiz: Path) -> None:
        adapter, _ = _armar(store=store, workdirs=_gestor(raiz, con_flag=False))
        await adapter.start()

        encolada = await _encolar_completo(adapter)
        fila = await store.get(encolada.task_id, "inaki")
        await adapter.stop()

        assert encolada.workdir is None
        assert encolada.workdir_unavailable is False
        assert not (raiz / "dwss").exists()
        assert fila is not None and fila.workdir is None

    async def test_sin_gestor_inyectado_no_se_crea_nada(self, store, raiz: Path) -> None:
        adapter, _ = _armar(store=store, workdirs=None)
        await adapter.start()

        encolada = await _encolar_completo(adapter)
        await adapter.stop()

        assert encolada.workdir is None and encolada.workdir_unavailable is False
        assert not (raiz / "dwss").exists()

    async def test_el_flag_es_por_caller(self, store, raiz: Path) -> None:
        """Triangulación: otro caller sin flag no recibe directorio aunque comparta cola."""
        gestor = GestorDeWorkdirs(workspaces={"inaki": raiz, "otro": raiz}, con_workdir=["inaki"])
        adapter, _ = _armar(store=store, workdirs=gestor)
        await adapter.start()

        con = await _encolar_completo(adapter, caller="inaki")
        sin = await _encolar_completo(adapter, caller="otro")
        await adapter.stop()

        assert con.workdir is not None
        assert sin.workdir is None

    async def test_dwss_no_escribible_encola_igual_sin_workdir_y_loguea_error(
        self, store, raiz: Path, caplog
    ) -> None:
        """REQ-BGW-2 S4: sin ``input_files`` es degradación ruidosa, no abortar."""
        caplog.set_level(logging.ERROR, logger=_LOGGER)
        (raiz / "dwss").write_text("soy un fichero, no un directorio")
        adapter, _ = _armar(store=store, workdirs=_gestor(raiz))
        await adapter.start()

        encolada = await _encolar_completo(adapter)
        fila = await store.get(encolada.task_id, "inaki")
        await adapter.stop()

        assert encolada.task_id == "bg-1000"  # la delegación SÍ se encoló
        assert encolada.workdir is None
        assert encolada.workdir_unavailable is True
        assert fila is not None and fila.workdir is None
        errores = [r.getMessage() for r in _errores(caplog)]
        assert len(errores) == 1
        assert "background task workdir unavailable" in errores[0]

    async def test_la_task_degradada_en_memoria_conserva_su_workdir(
        self, tmp_path: Path, raiz: Path
    ) -> None:
        """REQ-BGW-3: una task ``bg-m*`` (sin fila) mantiene la ruta en memoria."""
        adapter, _ = _armar(store=None, workdirs=_gestor(raiz))

        encolada = await _encolar_completo(adapter)

        assert encolada.task_id == "bg-m1"
        assert encolada.workdir is not None
        assert adapter._tasks["bg-m1"].workdir == encolada.workdir

    async def test_el_registro_en_creacion_se_limpia_al_existir_la_fila(
        self, store, raiz: Path
    ) -> None:
        adapter, _ = _armar(store=store, workdirs=_gestor(raiz))
        await adapter.start()

        await _encolar_completo(adapter)
        await adapter.stop()

        assert adapter._en_creacion == set()


# ---------------------------------------------------------------------------
# 4.12 — input_files: copia ANTES de encolar, validación previa, rechazo sin efectos
# ---------------------------------------------------------------------------


def _insumos(raiz: Path) -> None:
    (raiz / "notas").mkdir()
    (raiz / "notas" / "a.md").write_text("# a\n")
    (raiz / "datos.csv").write_text("x,y\n1,2\n")


class TestInputFiles:
    async def test_los_ficheros_estan_en_el_workdir_ANTES_de_crear_la_fila(
        self, store, raiz: Path
    ) -> None:
        """REQ-BGW-13 S1: el hijo los ve desde su primer paso; no hay carrera con el arranque."""
        _insumos(raiz)
        visto: dict = {}
        crear_real = store.create

        async def espiar(task):
            visto["workdir"] = task.workdir
            visto["contenido"] = sorted(p.name for p in Path(task.workdir).iterdir())
            visto["en_creacion"] = set(adapter._en_creacion)
            return await crear_real(task)

        store.create = espiar  # type: ignore[method-assign]
        adapter, _ = _armar(store=store, workdirs=_gestor(raiz))
        await adapter.start()

        encolada = await _encolar_completo(adapter, input_files=["notas/a.md", "datos.csv"])
        await adapter.stop()

        assert visto["contenido"] == ["a.md", "datos.csv"]
        assert visto["workdir"] in visto["en_creacion"]  # protegido del barrido mientras tanto
        assert encolada.workdir == visto["workdir"]
        assert encolada.input_files == ["a.md", "datos.csv"]
        assert (Path(encolada.workdir) / "a.md").read_text() == "# a\n"  # type: ignore[arg-type]
        assert (raiz / "notas" / "a.md").read_text() == "# a\n"  # el original no se toca

    async def test_la_fila_guarda_los_basenames_y_sobreviven_al_reinicio(
        self, store, raiz: Path
    ) -> None:
        """REQ-BGW-14 S2."""
        _insumos(raiz)
        adapter, _ = _armar(store=store, workdirs=_gestor(raiz))
        await adapter.start()
        encolada = await _encolar_completo(adapter, input_files=["notas/a.md", "datos.csv"])
        await adapter.stop()

        await store.open()
        fila = await store.get(encolada.task_id, "inaki")

        assert fila is not None
        assert fila.input_files == ["a.md", "datos.csv"]  # basenames, no las rutas pedidas
        assert fila.workdir == encolada.workdir

    async def test_la_copia_se_hace_fuera_del_event_loop(self, store, raiz: Path) -> None:
        """``preparar`` copia hasta 50 MiB de forma síncrona: nunca en el hilo del loop."""
        import threading

        _insumos(raiz)
        gestor = _gestor(raiz)
        hilos: list[int] = []
        preparar_real = gestor.preparar

        def espiar(caller, input_files):
            hilos.append(threading.get_ident())
            return preparar_real(caller, input_files)

        gestor.preparar = espiar  # type: ignore[method-assign]
        adapter, _ = _armar(store=store, workdirs=gestor)
        await adapter.start()

        await _encolar_completo(adapter, input_files=["datos.csv"])
        await adapter.stop()

        assert len(hilos) == 1
        assert hilos[0] != threading.get_ident()

    async def test_crear_el_workdir_sin_insumos_tambien_corre_fuera_del_event_loop(
        self, store, raiz: Path
    ) -> None:
        """``crear`` hace mkdir + ``secrets`` (E/S): el camino sin ``input_files`` no puede
        bloquear el loop que atiende a todos los canales, igual que el de ``preparar``."""
        import threading

        gestor = _gestor(raiz)
        hilos: list[int] = []
        crear_real = gestor.crear

        def espiar(caller):
            hilos.append(threading.get_ident())
            return crear_real(caller)

        gestor.crear = espiar  # type: ignore[method-assign]
        adapter, _ = _armar(store=store, workdirs=gestor)
        await adapter.start()

        encolada = await _encolar_completo(adapter)
        await adapter.stop()

        assert len(hilos) == 1
        assert hilos[0] != threading.get_ident()
        assert encolada.workdir is not None and Path(encolada.workdir).is_dir()

    async def test_sin_el_flag_rechaza_y_no_crea_ni_encola_nada(self, store, raiz: Path) -> None:
        """REQ-BGW-11 S2."""
        _insumos(raiz)
        adapter, dispatcher = _armar(store=store, workdirs=_gestor(raiz, con_flag=False))
        await adapter.start()

        with pytest.raises(InputFilesRejected) as exc_info:
            await _encolar_completo(adapter, input_files=["datos.csv"])
        filas = await store.list_by_status(["queued", "running"])
        await adapter.stop()

        assert exc_info.value.reason == "input_files_require_workdir"
        assert not (raiz / "dwss").exists()
        assert filas == []
        assert adapter._tasks == {} and adapter._queue.empty()

    async def test_sin_gestor_inyectado_rechaza_igual(self, store, raiz: Path) -> None:
        _insumos(raiz)
        adapter, _ = _armar(store=store, workdirs=None)
        await adapter.start()

        with pytest.raises(InputFilesRejected) as exc_info:
            await _encolar_completo(adapter, input_files=["datos.csv"])
        await adapter.stop()

        assert exc_info.value.reason == "input_files_require_workdir"

    @pytest.mark.parametrize(
        ("pedido", "reason"),
        [
            ("../../etc/passwd", "input_file_outside_workspace"),
            ("falta.md", "input_file_not_found"),
            ("notas", "input_file_not_a_file"),
        ],
    )
    async def test_un_insumo_invalido_rechaza_sin_crear_dir_ni_fila_ni_task(
        self, store, raiz: Path, pedido: str, reason: str
    ) -> None:
        """REQ-BGW-12: la validación corre ANTES de crear nada."""
        _insumos(raiz)
        adapter, _ = _armar(store=store, workdirs=_gestor(raiz))
        await adapter.start()

        with pytest.raises(InputFilesRejected) as exc_info:
            await _encolar_completo(adapter, input_files=["datos.csv", pedido])
        filas = await store.list_by_status(["queued"])
        await adapter.stop()

        assert exc_info.value.reason == reason
        assert exc_info.value.path == pedido
        assert not (raiz / "dwss").exists()
        assert filas == [] and adapter._tasks == {}
        assert adapter._en_creacion == set()

    async def test_dwss_no_escribible_con_insumos_rechaza_en_lugar_de_degradar(
        self, store, raiz: Path
    ) -> None:
        """REQ-BGW-13 S3: con insumos NO se encola sin workdir (sería arrancar sin lo prometido)."""
        _insumos(raiz)
        (raiz / "dwss").write_text("soy un fichero, no un directorio")
        adapter, _ = _armar(store=store, workdirs=_gestor(raiz))
        await adapter.start()

        with pytest.raises(InputFilesRejected) as exc_info:
            await _encolar_completo(adapter, input_files=["datos.csv"])
        filas = await store.list_by_status(["queued"])
        await adapter.stop()

        assert exc_info.value.reason == "input_files_workdir_unavailable"
        assert filas == [] and adapter._tasks == {}

    async def test_un_aborto_despues_de_copiar_no_deja_el_directorio_huerfano(
        self, store, raiz: Path
    ) -> None:
        """Si el registro de la fila se cancela con los insumos ya copiados, el workdir se
        borra: no queda un directorio que nadie referencia."""
        _insumos(raiz)

        async def cancelado(task):
            raise asyncio.CancelledError

        store.create = cancelado  # type: ignore[method-assign]
        adapter, _ = _armar(store=store, workdirs=_gestor(raiz))
        await adapter.start()

        with pytest.raises(asyncio.CancelledError):
            await _encolar_completo(adapter, input_files=["datos.csv"])
        await adapter.stop()

        assert list((raiz / "dwss").iterdir()) == []
        assert adapter._en_creacion == set()


# ---------------------------------------------------------------------------
# 4.2 — _run_task: recorder, footer, resultado estructurado, persistido ANTES del dispatch
# ---------------------------------------------------------------------------

_JSON_OK = '```json\n{"status": "success", "summary": "Saldo $5.420", "details": "cuenta 1: 5420", "reason": null}\n```'
_JSON_FALLO = '```json\n{"status": "failed", "summary": "no pude entrar", "details": null, "reason": "login_denied"}\n```'


async def _correr(adapter, **kw) -> str:
    """Start + encolar + dejar correr + stop. Devuelve el task_id.

    ``stop`` cierra el store; se lo reabre para que el test pueda leer las filas (equivale
    a mirar el fichero tras un reinicio)."""
    await adapter.start()
    task_id = await _encolar(adapter, **kw)
    await asyncio.sleep(0.15)
    await adapter.stop()
    if adapter._store is not None:
        await adapter._store.open()
    return task_id


class TestRunTaskContrato:
    async def test_el_hijo_recibe_el_footer_sobre_el_prompt_del_caller(self, store) -> None:
        """REQ-BGD-5 / REQ-DG-11: el path async lleva el mismo contrato JSON que el sync."""
        hijo = _one_shot(_JSON_OK)
        adapter, _ = _armar(store=store, one_shot_for={"r": hijo})
        await adapter.start()
        await adapter.enqueue(
            caller_agent_id="inaki",
            target_agent_id="r",
            prompt="x",
            system_prompt="Prompt del caller.",
            channel="telegram",
            chat_id="42",
            max_iterations=5,
            timeout_seconds=30,
        )
        await asyncio.sleep(0.1)
        await adapter.stop()

        enviado = hijo.execute.await_args.kwargs["system_prompt"]
        assert enviado == "Prompt del caller.\n\n" + RESULT_FORMAT_FOOTER

    async def test_sin_system_prompt_del_caller_el_footer_va_sobre_el_default_del_hijo(
        self, store
    ) -> None:
        """REQ-BGD-5 S3."""
        hijo = _one_shot(_JSON_OK, system_prompt="Default del sub-agente.")
        adapter, _ = _armar(store=store, one_shot_for={"r": hijo})

        await _correr(adapter)

        enviado = hijo.execute.await_args.kwargs["system_prompt"]
        assert enviado == "Default del sub-agente.\n\n" + RESULT_FORMAT_FOOTER

    async def test_la_fila_conserva_el_system_prompt_del_caller_sin_footer(self, store) -> None:
        adapter, _ = _armar(store=store, one_shot_for={"r": _one_shot(_JSON_OK)})
        await adapter.start()
        task_id = (
            await adapter.enqueue(
                caller_agent_id="inaki",
                target_agent_id="r",
                prompt="x",
                system_prompt="Prompt del caller.",
                channel="",
                chat_id="",
                max_iterations=5,
                timeout_seconds=30,
            )
        ).task_id
        await adapter.stop()
        await store.open()

        assert (await store.get(task_id, "inaki")).system_prompt == "Prompt del caller."

    async def test_el_hijo_recibe_un_recorder_ligado_a_la_fila(self, store) -> None:
        """REQ-BGP-2: lo que el hijo anota en el recorder queda en el transcript durable."""

        async def hijo_que_anota(**kw):
            await kw["recorder"].record(Message(role=Role.USER, content="paso 1"))
            return _JSON_OK

        hijo = _one_shot()
        hijo.execute = AsyncMock(side_effect=hijo_que_anota)
        adapter, _ = _armar(store=store, one_shot_for={"r": hijo})
        task_id = await _correr(adapter)

        pasos, total = await store.messages(task_id, last_n=10)

        assert total == 1
        assert pasos[0].content == "paso 1"

    async def test_sin_store_el_recorder_es_nulo_pero_el_hijo_corre_igual(self) -> None:
        hijo = _one_shot(_JSON_OK)
        adapter, dispatcher = _armar(store=None, one_shot_for={"r": hijo})

        await _correr(adapter)

        assert hijo.execute.await_args.kwargs["recorder"].trace == []
        dispatcher.dispatch.assert_awaited_once()


class TestResultadoEstructurado:
    async def test_un_resultado_con_json_llega_estructurado_al_padre(self, store) -> None:
        """REQ-BGD-5 S1."""
        adapter, dispatcher = _armar(store=store, one_shot_for={"r": _one_shot(_JSON_OK)})

        task_id = await _correr(adapter)

        prompt = dispatcher.dispatch.await_args.kwargs["prompt"]
        assert prompt == f"[{task_id}] success: Saldo $5.420\n\ncuenta 1: 5420"

    async def test_la_fila_guarda_el_mensaje_exacto_el_status_del_hijo_y_el_cierre(
        self, store
    ) -> None:
        adapter, dispatcher = _armar(store=store, one_shot_for={"r": _one_shot(_JSON_OK)})

        task_id = await _correr(adapter)
        fila = await store.get(task_id, "inaki")

        assert fila.status == "delivered"
        assert fila.result == dispatcher.dispatch.await_args.kwargs["prompt"]
        assert fila.result_status == "success"
        assert fila.result_parse_error is False
        assert fila.finished_at is not None
        assert fila.run_started_at is not None and fila.run_started_at <= fila.finished_at

    async def test_un_fallo_declarado_por_el_hijo_se_entrega_como_tal(self, store) -> None:
        adapter, dispatcher = _armar(store=store, one_shot_for={"r": _one_shot(_JSON_FALLO)})

        task_id = await _correr(adapter)

        prompt = dispatcher.dispatch.await_args.kwargs["prompt"]
        assert prompt.startswith(
            f"[{task_id}] failed (reported by the child, reason: login_denied): no pude entrar"
        )
        assert (await store.get(task_id, "inaki")).result_status == "failed"

    async def test_sin_bloque_json_llega_el_texto_crudo_marcado_result_parse_error(
        self, store
    ) -> None:
        """REQ-BGD-5 S2: el trabajo del hijo no se pierde por no respetar el formato."""
        adapter, dispatcher = _armar(
            store=store, one_shot_for={"r": _one_shot("Te hice un informe largo.")}
        )

        task_id = await _correr(adapter)
        fila = await store.get(task_id, "inaki")

        prompt = dispatcher.dispatch.await_args.kwargs["prompt"]
        assert prompt.startswith(f"[{task_id}] completed, but without the required JSON")
        assert prompt.endswith("Te hice un informe largo.")
        assert fila.result_parse_error is True
        assert fila.result_status is None


class TestResultadoAntesDelDispatch:
    async def test_cuando_corre_el_dispatch_la_fila_ya_tiene_el_resultado(self, store) -> None:
        """REQ-BGP-4: si el daemon muere durante la entrega, el resultado ya está a salvo."""
        visto: dict = {}
        adapter, dispatcher = _armar(store=store, one_shot_for={"r": _one_shot(_JSON_OK)})

        async def espiar(**kw):
            visto["fila"] = await store.get(visto["id"], "inaki")
            visto["prompt"] = kw["prompt"]
            return ""

        dispatcher.dispatch = AsyncMock(side_effect=espiar)
        await adapter.start()
        visto["id"] = await _encolar(adapter)
        await asyncio.sleep(0.15)
        await adapter.stop()

        assert visto["fila"].status == "delivering"
        assert visto["fila"].result == visto["prompt"]
        assert visto["fila"].result_status == "success"

    async def test_si_el_dispatch_falla_la_fila_queda_delivery_failed_con_el_resultado(
        self, store
    ) -> None:
        """REQ-BGP-4 S1 + bg-stuck-task: una entrega fallida no es ``running`` ni se pierde."""
        adapter, dispatcher = _armar(store=store, one_shot_for={"r": _one_shot(_JSON_OK)})
        dispatcher.dispatch = AsyncMock(side_effect=RuntimeError("provider caído"))
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(2.0)  # agota los 3 intentos (backoff 0.5 + 1.0)
        fila = await store.get(task_id, "inaki")
        await adapter.stop()

        assert fila.status == "delivery_failed"
        assert fila.error == "RuntimeError: provider caído"
        assert fila.result == f"[{task_id}] success: Saldo $5.420\n\ncuenta 1: 5420"
        assert task_id in adapter._tasks  # sigue viva para el padre

    async def test_el_return_del_dispatch_se_entrega_al_canal(self, store) -> None:
        """El valor de retorno del dispatch NUNCA se descarta (bg-result-delivery)."""
        sender = MagicMock()
        sender.send_message = AsyncMock()
        sender.build_intermediate_sink = MagicMock(return_value=None)
        sender.is_conversational = MagicMock(return_value=True)
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="Listo: el informe quedó guardado")
        adapter = BackgroundDelegationQueueAdapter(
            dispatcher=dispatcher,
            one_shot_resolver=lambda caller, target: _one_shot(_JSON_OK),
            result_sender=sender,
            store=store,
        )

        await _correr(adapter)

        sender.send_message.assert_awaited_once_with(
            "telegram:42",
            "Listo: el informe quedó guardado",
            agent_id="inaki",
            record_history=False,
        )


# ---------------------------------------------------------------------------
# 4.9 — el hijo recibe la instrucción del workdir; el éxito la menciona (REQ-BGW-4/10/14)
# ---------------------------------------------------------------------------


class TestPromptDelHijoConWorkdir:
    async def test_el_mensaje_de_tarea_lleva_ruta_relativa_absoluta_y_los_insumos(
        self, store, raiz: Path
    ) -> None:
        _insumos(raiz)
        hijo = _one_shot(_JSON_OK)
        adapter, _ = _armar(store=store, one_shot_for={"r": hijo}, workdirs=_gestor(raiz))
        await adapter.start()

        encolada = await _encolar_completo(
            adapter, prompt="analizá los datos", input_files=["notas/a.md", "datos.csv"]
        )
        await asyncio.sleep(0.15)
        await adapter.stop()

        enviado = hijo.execute.await_args.kwargs["task"]
        relativa = f"dwss/{Path(encolada.workdir).name}/"  # type: ignore[arg-type]
        assert enviado.startswith("analizá los datos")
        assert f"`{relativa}`" in enviado
        assert f"`{encolada.workdir}`" in enviado
        assert "Input files provided for this task: a.md, datos.csv" in enviado

    async def test_sin_insumos_la_instruccion_no_menciona_input_files(
        self, store, raiz: Path
    ) -> None:
        hijo = _one_shot(_JSON_OK)
        adapter, _ = _armar(store=store, one_shot_for={"r": hijo}, workdirs=_gestor(raiz))

        await _correr(adapter)

        enviado = hijo.execute.await_args.kwargs["task"]
        assert "Working directory for this task" in enviado
        assert "Input files provided" not in enviado

    async def test_sin_workdir_el_prompt_es_identico_al_de_hoy(self, store, raiz: Path) -> None:
        hijo = _one_shot(_JSON_OK)
        adapter, _ = _armar(
            store=store, one_shot_for={"r": hijo}, workdirs=_gestor(raiz, con_flag=False)
        )

        await _correr(adapter, prompt="investigá X")

        assert hijo.execute.await_args.kwargs["task"] == "investigá X"

    async def test_la_fila_guarda_el_prompt_original_sin_la_instruccion(
        self, store, raiz: Path
    ) -> None:
        adapter, _ = _armar(
            store=store, one_shot_for={"r": _one_shot(_JSON_OK)}, workdirs=_gestor(raiz)
        )

        task_id = await _correr(adapter, prompt="investigá X")

        assert (await store.get(task_id, "inaki")).prompt == "investigá X"

    async def test_el_exito_menciona_el_workdir_en_lo_que_recibe_el_padre(
        self, store, raiz: Path
    ) -> None:
        """REQ-BGW-10 (SHOULD): el padre sabe dónde buscar lo que el hijo produjo."""
        adapter, dispatcher = _armar(
            store=store, one_shot_for={"r": _one_shot(_JSON_OK)}, workdirs=_gestor(raiz)
        )

        task_id = await _correr(adapter)

        fila = await store.get(task_id, "inaki")
        relativa = f"dwss/{Path(fila.workdir).name}/"  # type: ignore[arg-type]
        prompt = dispatcher.dispatch.await_args.kwargs["prompt"]
        assert prompt.endswith(
            f"\n\nWorking directory: {relativa} (deleted when this task is pruned)."
        )
        assert fila.result == prompt  # lo persistido es lo entregado

    async def test_un_resultado_sin_json_tambien_menciona_el_workdir(
        self, store, raiz: Path
    ) -> None:
        adapter, dispatcher = _armar(
            store=store, one_shot_for={"r": _one_shot("prosa sin json")}, workdirs=_gestor(raiz)
        )

        await _correr(adapter)

        assert "Working directory: dwss/" in dispatcher.dispatch.await_args.kwargs["prompt"]

    async def test_sin_workdir_el_resultado_no_menciona_ningun_directorio(
        self, store, raiz: Path
    ) -> None:
        adapter, dispatcher = _armar(
            store=store,
            one_shot_for={"r": _one_shot(_JSON_OK)},
            workdirs=_gestor(raiz, con_flag=False),
        )

        await _correr(adapter)

        assert "Working directory" not in dispatcher.dispatch.await_args.kwargs["prompt"]


# ---------------------------------------------------------------------------
# 4.3 — fallos clasificados y honestos (REQ-BGP-5/6)
# ---------------------------------------------------------------------------


def _hijo_que_lanza(exc: BaseException) -> MagicMock:
    hijo = _one_shot()
    hijo.execute = AsyncMock(side_effect=exc)
    return hijo


class TestFallosClasificados:
    async def test_una_excepcion_se_reporta_con_tipo_mensaje_y_hint_de_inspect(self, store) -> None:
        adapter, dispatcher = _armar(
            store=store, one_shot_for={"r": _hijo_que_lanza(RuntimeError("boom"))}
        )

        task_id = await _correr(adapter)

        prompt = dispatcher.dispatch.await_args.kwargs["prompt"]
        assert prompt.startswith(
            f"[{task_id}] failed (exception): the child agent stopped with an error."
        )
        assert "- error: RuntimeError: boom" in prompt
        assert f"background_tasks(action=inspect, task_id={task_id})" in prompt

    async def test_una_excepcion_sin_mensaje_nunca_deja_el_detalle_vacio(self, store) -> None:
        """REQ-BGP-5 S3: el bug original era ``failed: TimeoutError: `` con el texto en blanco."""
        adapter, dispatcher = _armar(
            store=store, one_shot_for={"r": _hijo_que_lanza(RuntimeError(""))}
        )

        await _correr(adapter)

        assert (
            "- error: RuntimeError (no message)" in dispatcher.dispatch.await_args.kwargs["prompt"]
        )

    async def test_un_timeout_se_clasifica_con_el_presupuesto_gastado(self, store) -> None:
        """REQ-BGP-5 S1."""
        adapter, dispatcher = _armar(
            store=store, one_shot_for={"r": _hijo_que_lanza(TimeoutError(""))}
        )
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.15)
        await adapter.stop()
        await store.open()

        prompt = dispatcher.dispatch.await_args.kwargs["prompt"]
        assert prompt.startswith(
            f"[{task_id}] failed (timeout): the child agent ran out of time (30s) before finishing."
        )
        assert "- budget: 30s / 5 iterations — used " in prompt
        fila = await store.get(task_id, "inaki")
        assert fila.failure is not None and fila.failure.kind == "timeout"
        assert fila.failure.detail == "TimeoutError (no message)"

    async def test_max_iterations_cita_lo_ultimo_que_dijo_el_hijo(self, store) -> None:
        """REQ-BGP-5 S2."""
        adapter, dispatcher = _armar(
            store=store,
            one_shot_for={"r": _hijo_que_lanza(ToolLoopMaxIterationsError("sigo buscando"))},
        )

        task_id = await _correr(adapter)

        prompt = dispatcher.dispatch.await_args.kwargs["prompt"]
        assert (
            f"[{task_id}] failed (max_iterations): the child agent used all 5 iterations" in prompt
        )
        assert "last step: said: «sigo buscando»" in prompt
        fila = await store.get(task_id, "inaki")
        assert fila.failure.kind == "max_iterations"  # type: ignore[union-attr]
        assert fila.failure.iterations_used == 5  # type: ignore[union-attr]

    async def test_un_fallo_se_persiste_clasificado_y_la_task_termina_entregada(
        self, store
    ) -> None:
        adapter, _ = _armar(store=store, one_shot_for={"r": _hijo_que_lanza(ValueError("nope"))})

        task_id = await _correr(adapter)
        fila = await store.get(task_id, "inaki")

        assert fila.status == "delivered"
        assert fila.failure is not None
        assert (fila.failure.kind, fila.failure.detail) == ("exception", "ValueError: nope")
        assert fila.result_status is None  # no hubo respuesta del hijo que parsear
        assert fila.result is not None and "failed (exception)" in fila.result

    async def test_target_desconocido_no_tiene_transcript_ni_hint_de_inspect(self, store) -> None:
        adapter, dispatcher = _armar(store=store, one_shot_for={})
        await adapter.start()
        task_id = await _encolar(adapter, target="fantasma")
        await asyncio.sleep(0.15)
        await adapter.stop()
        await store.open()

        prompt = dispatcher.dispatch.await_args.kwargs["prompt"]
        fila = await store.get(task_id, "inaki")
        assert (
            prompt == f"[{task_id}] failed (unknown_target): 'fantasma' is not a known sub-agent."
        )
        assert fila.failure.kind == "unknown_target"  # type: ignore[union-attr]
        assert (await store.messages(task_id, last_n=10))[1] == 0

    async def test_la_cancelacion_no_se_reporta_como_fallo(self, store) -> None:
        """``CancelledError`` no es un fallo del hijo: ``cancel`` no dispatcha nada."""
        hijo = _one_shot()

        async def eterno(**_kw):
            await asyncio.sleep(10)

        hijo.execute = AsyncMock(side_effect=eterno)
        adapter, dispatcher = _armar(store=store, one_shot_for={"r": hijo})
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.05)

        await adapter.cancel(task_id, "inaki")
        await asyncio.sleep(0.05)
        await adapter.stop()

        dispatcher.dispatch.assert_not_called()


def _tool_call(call_id: str, name: str) -> dict:
    return {"id": call_id, "function": {"name": name, "arguments": '{"command": "rm -rf /tmp/x"}'}}


def _hijo_real_colgado_en_una_tool() -> RunAgentOneShotUseCase:
    """One-shot REAL (run_tool_loop incluido) cuyo LLM pide dos tools y la primera se cuelga."""
    llm = AsyncMock()
    llm.complete.return_value = LLMResponse(
        text_blocks=["voy a limpiar"],
        tool_calls=[_tool_call("c1", "shell_exec"), _tool_call("c2", "write_file")],
    )
    tools = MagicMock()
    tools.get_schemas.return_value = []
    tools.requires_operator.return_value = False

    async def se_cuelga(*_a, **_kw):
        await asyncio.sleep(30)

    tools.execute = AsyncMock(side_effect=se_cuelga)
    settings = OneShotSettings(
        agent_id="r",
        system_prompt="Sos el hijo.",
        circuit_breaker_threshold=2,
        request_delay_seconds=0.0,
    )
    return RunAgentOneShotUseCase(llm=llm, tools=tools, settings=settings)


class TestTimeoutAMitadDeUnaTool:
    """REQ-BGP-6: el caso real que originó el cambio. El padre leyó ``TimeoutError`` sin
    mensaje y concluyó que no se había ejecutado nada; la tool SÍ había corrido."""

    async def test_el_reporte_nombra_la_tool_en_vuelo_y_dice_que_el_efecto_es_desconocido(
        self, store
    ) -> None:
        adapter, dispatcher = _armar(
            store=store, one_shot_for={"r": _hijo_real_colgado_en_una_tool()}
        )
        await adapter.start()
        task_id = await _encolar(adapter, timeout_seconds=1)
        await asyncio.sleep(1.6)
        await adapter.stop()
        await store.open()

        prompt = dispatcher.dispatch.await_args.kwargs["prompt"]
        assert prompt.startswith(f"[{task_id}] failed (timeout)")
        assert "tool call in flight: shell_exec(" in prompt
        assert "outcome unknown, it may have had side effects" in prompt
        assert "Verify before retrying" in prompt
        assert "- not started: write_file" in prompt
        assert "last step: called shell_exec, write_file" in prompt
        assert "did not run" not in prompt and "not executed" not in prompt

    async def test_el_transcript_durable_conserva_la_tool_call_sin_respuesta(self, store) -> None:
        adapter, _ = _armar(store=store, one_shot_for={"r": _hijo_real_colgado_en_una_tool()})
        await adapter.start()
        task_id = await _encolar(adapter, timeout_seconds=1)
        await asyncio.sleep(1.6)
        await adapter.stop()
        await store.open()

        pasos, total = await store.messages(task_id, last_n=10)
        fila = await store.get(task_id, "inaki")

        assert [p.role.value for p in pasos] == ["user", "assistant"]
        assert [tc["id"] for tc in pasos[1].tool_calls or []] == ["c1", "c2"]
        assert total == 2
        assert fila.failure.in_flight_tool.name == "shell_exec"  # type: ignore[union-attr]
        assert fila.failure.not_started_tools == ["write_file"]  # type: ignore[union-attr]

    async def test_sin_store_el_reporte_sale_igual_de_honesto_desde_memoria(self) -> None:
        """El reporte NO depende del disco: sale del ``trace`` en memoria."""
        adapter, dispatcher = _armar(
            store=None, one_shot_for={"r": _hijo_real_colgado_en_una_tool()}
        )
        await adapter.start()
        await _encolar(adapter, timeout_seconds=1)
        await asyncio.sleep(1.6)
        await adapter.stop()

        prompt = dispatcher.dispatch.await_args.kwargs["prompt"]
        assert "tool call in flight: shell_exec(" in prompt
        assert "outcome unknown, it may have had side effects" in prompt


class TestFalloConWorkdir:
    async def test_el_fallo_de_una_task_con_workdir_lo_menciona(self, store, raiz: Path) -> None:
        """REQ-BGW-10 S1 de punta a punta: cola → clasificación → mensaje al padre."""
        adapter, dispatcher = _armar(
            store=store,
            one_shot_for={"r": _hijo_que_lanza(RuntimeError("boom"))},
            workdirs=_gestor(raiz),
        )

        task_id = await _correr(adapter)

        fila = await store.get(task_id, "inaki")
        prompt = dispatcher.dispatch.await_args.kwargs["prompt"]
        assert f"- workdir: dwss/{Path(fila.workdir).name}/" in prompt  # type: ignore[arg-type]
        assert "partial outputs may be there" in prompt


# ---------------------------------------------------------------------------
# 4.4 — cancel async con persistencia (REQ-BGP-4 S2, REQ-BGD-10 S1/S2, REQ-BGD-8 S3)
# ---------------------------------------------------------------------------


def _hijo_lento(segundos: float = 10.0) -> MagicMock:
    hijo = _one_shot()

    async def lento(**_kw):
        await asyncio.sleep(segundos)
        return _JSON_OK

    hijo.execute = AsyncMock(side_effect=lento)
    return hijo


class TestCancelPersistente:
    async def test_cancelar_una_queued_la_deja_cancelled_sin_mensajes_ni_dispatch(
        self, store
    ) -> None:
        """REQ-BGP-4 S2."""
        hijo = _hijo_lento()
        adapter, dispatcher = _armar(store=store, one_shot_for={"r": hijo}, max_concurrent=1)
        await adapter.start()
        primera = await _encolar(adapter)
        segunda = await _encolar(adapter)  # queda queued: el semáforo está tomado
        await asyncio.sleep(0.05)

        res = await adapter.cancel(segunda, "inaki")
        fila = await store.get(segunda, "inaki")
        await adapter.cancel(primera, "inaki")
        await asyncio.sleep(0.05)
        pasos = await store.messages(segunda, last_n=10)
        await adapter.stop()

        assert res.outcome == "cancelled"
        assert fila.status == "cancelled"
        assert fila.finished_at is not None
        assert fila.result is None and fila.failure is None
        assert pasos == ([], 0)
        dispatcher.dispatch.assert_not_called()
        assert hijo.execute.await_count == 1  # solo la primera llegó a correr

    async def test_cancelar_una_running_persiste_cancelled_y_no_dispatcha(self, store) -> None:
        adapter, dispatcher = _armar(store=store, one_shot_for={"r": _hijo_lento()})
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.05)
        assert (await store.get(task_id, "inaki")).status == "running"

        res = await adapter.cancel(task_id, "inaki")
        await asyncio.sleep(0.05)
        fila = await store.get(task_id, "inaki")
        await adapter.stop()

        assert res.outcome == "cancelled"
        assert fila.status == "cancelled"
        assert task_id not in adapter._tasks and task_id not in adapter._handles
        dispatcher.dispatch.assert_not_called()

    async def test_descartar_una_delivery_failed_devuelve_el_resultado_y_queda_dismissed(
        self, store
    ) -> None:
        """REQ-BGD-10 S1."""
        adapter, dispatcher = _armar(store=store, one_shot_for={"r": _one_shot(_JSON_OK)})
        dispatcher.dispatch = AsyncMock(side_effect=RuntimeError("provider caído"))
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(2.0)

        res = await adapter.cancel(task_id, "inaki")
        fila = await store.get(task_id, "inaki")
        await adapter.stop()

        assert res.outcome == "dismissed"
        assert res.result == f"[{task_id}] success: Saldo $5.420\n\ncuenta 1: 5420"
        assert res.error == "RuntimeError: provider caído"
        assert fila.status == "dismissed"
        assert fila.result == res.result  # el resultado sigue en la fila (inspect)
        assert adapter.snapshot_inflight("inaki") == []

    async def test_mientras_se_entrega_no_se_cancela_y_la_fila_sigue_delivering(
        self, store
    ) -> None:
        """REQ-BGD-10 S2."""
        liberar = asyncio.Event()
        adapter, dispatcher = _armar(store=store, one_shot_for={"r": _one_shot(_JSON_OK)})

        async def lento(**_kw):
            await liberar.wait()
            return ""

        dispatcher.dispatch = AsyncMock(side_effect=lento)
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.1)

        res = await adapter.cancel(task_id, "inaki")
        fila = await store.get(task_id, "inaki")
        liberar.set()
        await asyncio.sleep(0.1)
        final = await store.get(task_id, "inaki")
        await adapter.stop()

        assert res.outcome == "delivering"
        assert fila.status == "delivering"
        assert final.status == "delivered"

    async def test_cancelar_una_entregada_responde_finished_delivered(self, store) -> None:
        adapter, _ = _armar(store=store, one_shot_for={"r": _one_shot(_JSON_OK)})
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.15)  # termina y se entrega

        res = await adapter.cancel(task_id, "inaki")
        await adapter.stop()

        assert (res.outcome, res.status, res.task_id) == ("finished", "delivered", task_id)

    async def test_cancelar_dos_veces_no_cambia_nada_la_segunda(self, store) -> None:
        adapter, _ = _armar(store=store, one_shot_for={"r": _hijo_lento()})
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.05)
        await adapter.cancel(task_id, "inaki")

        segunda = await adapter.cancel(task_id, "inaki")
        fila = await store.get(task_id, "inaki")
        await adapter.stop()

        assert (segunda.outcome, segunda.status) == ("finished", "cancelled")
        assert fila.status == "cancelled"

    async def test_el_id_de_otro_caller_es_indistinguible_de_uno_inexistente(self, store) -> None:
        """No se revela la existencia de la task ajena, ni siquiera si ya terminó."""
        adapter, _ = _armar(store=store, one_shot_for={"r": _one_shot(_JSON_OK)})
        await adapter.start()
        task_id = await _encolar(adapter, caller="inaki")
        await asyncio.sleep(0.15)  # termina y queda delivered en el store

        ajena = await adapter.cancel(task_id, "anacleto")
        inexistente = await adapter.cancel("bg-4242", "anacleto")
        fila = await store.get(task_id, "inaki")
        await adapter.stop()

        assert fila.status == "delivered"
        assert ajena.outcome == inexistente.outcome == "not_found"
        assert ajena.status is None and ajena.result is None

    async def test_un_id_en_memoria_que_no_esta_en_el_dict_es_not_found_sin_tocar_el_store(
        self, tmp_path: Path
    ) -> None:
        roto = StoreRoto(tmp_path / "bg.db")
        adapter, _ = _armar(store=roto)
        await adapter.start()

        res = await adapter.cancel("bg-m7", "inaki")
        await adapter.stop()

        assert res.outcome == "not_found"

    async def test_sin_store_la_terminada_es_not_found(self) -> None:
        adapter, _ = _armar(store=None, one_shot_for={"r": _one_shot(_JSON_OK)})
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.1)

        res = await adapter.cancel(task_id, "inaki")
        await adapter.stop()

        assert res.outcome == "not_found"

    async def test_la_decision_y_la_mutacion_del_dict_ocurren_antes_del_primer_await(
        self, store
    ) -> None:
        """Con el store bloqueado en el UPDATE, el dict ya refleja la cancelación: nadie puede
        verla viva ni cancelarla dos veces en la ventana de la escritura."""
        adapter, dispatcher = _armar(store=store, one_shot_for={"r": _hijo_lento()})
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.05)
        liberar = asyncio.Event()
        update_real = store.update

        async def update_bloqueado(task_id_, **campos):
            await liberar.wait()
            return await update_real(task_id_, **campos)

        store.update = update_bloqueado  # type: ignore[method-assign]
        pendiente = asyncio.create_task(adapter.cancel(task_id, "inaki"))
        await asyncio.sleep(0)  # un solo turno del loop: cancel corrió hasta su primer await

        visible = adapter.snapshot_inflight("inaki")
        handle = adapter._handles.get(task_id)
        liberar.set()
        res = await pendiente
        await adapter.stop()

        assert visible == []
        assert res.outcome == "cancelled"
        assert handle is None or handle.cancelled() or handle.cancelling() > 0
        dispatcher.dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# 4.5 — stop() / barrido de arranque (REQ-BGD-8 S1, REQ-BGD-9, REQ-BGD-10)
# ---------------------------------------------------------------------------

_DETALLE_STOP = "the daemon stopped (reload or shutdown)"
_DETALLE_ARRANQUE = "found unfinished at startup (crash or kill)"
_ENTREGA_CORTADA = "delivery interrupted by a daemon stop; it may or may not have reached you"


def _fila_nueva(
    *, caller: str = "inaki", target: str = "r", prompt: str = "investigá X", chat_id: str = "42"
):
    return BackgroundTask(
        id="",
        caller_agent_id=caller,
        target_agent_id=target,
        prompt=prompt,
        system_prompt=None,
        channel="telegram",
        chat_id=chat_id,
        max_iterations=5,
        timeout_seconds=30,
        started_at=datetime.now(timezone.utc),
        status="queued",
    )


async def _sembrar(store, estado: str, **campos) -> str:
    """Una fila tal como la dejaría un daemon que murió sin ``stop()`` (SIGKILL)."""
    await store.open()
    task_id = await store.create(
        _fila_nueva(
            **{k: campos.pop(k) for k in ("caller", "target", "prompt", "chat_id") if k in campos}
        )
    )
    extra = dict(campos)
    if estado in ("running", "delivering"):
        extra.setdefault("run_started_at", datetime.now(timezone.utc))
    await store.update(task_id, status=estado, **extra)
    return task_id


async def _stop_y_leer(adapter, store, task_id: str, caller: str = "inaki"):
    await adapter.stop()
    await store.open()
    return await store.get(task_id, caller)


class TestStopMarcaInterrumpidas:
    async def test_stop_marca_interrupted_en_el_dict_ANTES_de_cancelar_al_hijo(self, store) -> None:
        """REQ-BGD-8 S1: lo primero que ve el hijo cortado es que su task ya es ``interrupted``."""
        visto: dict = {}
        hijo = _one_shot()

        async def colgado(**_kw):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                visto["estado"] = adapter._tasks[visto["id"]].status
                raise

        hijo.execute = AsyncMock(side_effect=colgado)
        adapter, dispatcher = _armar(store=store, one_shot_for={"r": hijo})
        await adapter.start()
        visto["id"] = await _encolar(adapter)
        await asyncio.sleep(0.05)

        await adapter.stop()

        assert visto["estado"] == "interrupted"
        dispatcher.dispatch.assert_not_called()

    async def test_stop_deja_la_fila_interrupted_sin_avisar_y_con_el_motivo(self, store) -> None:
        """REQ-BGD-9 S1."""
        adapter, dispatcher = _armar(store=store, one_shot_for={"r": _hijo_lento()})
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.05)

        fila = await _stop_y_leer(adapter, store, task_id)

        assert fila.status == "interrupted"
        assert fila.notified is False
        assert fila.finished_at is not None and fila.run_started_at is not None
        assert fila.failure is not None
        assert (fila.failure.kind, fila.failure.detail) == ("interrupted", _DETALLE_STOP)
        assert fila.result is None
        dispatcher.dispatch.assert_not_called()

    async def test_una_queued_tambien_queda_interrupted_y_dice_que_nunca_arranco(
        self, store
    ) -> None:
        """REQ-BGD-9 S2."""
        adapter, _ = _armar(store=store, one_shot_for={"r": _hijo_lento()}, max_concurrent=1)
        await adapter.start()
        await _encolar(adapter)
        en_cola = await _encolar(adapter)
        await asyncio.sleep(0.05)

        fila = await _stop_y_leer(adapter, store, en_cola)

        assert fila.status == "interrupted"
        assert fila.notified is False
        assert fila.run_started_at is None
        assert fila.failure.last_step == ""  # type: ignore[union-attr]

    async def test_el_corte_conserva_el_ultimo_paso_y_la_tool_en_vuelo(self, store) -> None:
        """REQ-BGP-6 también para una interrupción: ``shell_exec`` quedó a medias."""
        adapter, _ = _armar(store=store, one_shot_for={"r": _hijo_real_colgado_en_una_tool()})
        await adapter.start()
        task_id = await _encolar(adapter)  # timeout de 30 s: el que corta es stop()
        await asyncio.sleep(0.15)

        fila = await _stop_y_leer(adapter, store, task_id)

        assert fila.failure.in_flight_tool.name == "shell_exec"  # type: ignore[union-attr]
        assert fila.failure.not_started_tools == ["write_file"]  # type: ignore[union-attr]
        assert fila.failure.last_step == "called shell_exec, write_file"  # type: ignore[union-attr]
        assert fila.iterations_used == 1

    async def test_una_task_en_delivering_pasa_a_delivery_failed_conservando_el_resultado(
        self, store
    ) -> None:
        """REQ-BGD-10 S1: no se re-dispatcha sola (duplicaría el turno del padre)."""
        entrando = asyncio.Event()
        adapter, dispatcher = _armar(store=store, one_shot_for={"r": _one_shot(_JSON_OK)})

        async def dispatch_colgado(**_kw):
            entrando.set()
            await asyncio.sleep(10)

        dispatcher.dispatch = AsyncMock(side_effect=dispatch_colgado)
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.wait_for(entrando.wait(), timeout=2)

        fila = await _stop_y_leer(adapter, store, task_id)

        assert fila.status == "delivery_failed"
        assert fila.result == f"[{task_id}] success: Saldo $5.420\n\ncuenta 1: 5420"
        assert fila.error == _ENTREGA_CORTADA
        assert fila.notified is False
        assert dispatcher.dispatch.await_count == 1

    async def test_las_terminadas_no_se_tocan(self, store) -> None:
        adapter, _ = _armar(store=store, one_shot_for={"r": _one_shot(_JSON_OK)})
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.15)

        fila = await _stop_y_leer(adapter, store, task_id)

        assert fila.status == "delivered"
        assert fila.notified is True and fila.failure is None

    async def test_stop_cierra_el_store(self, store) -> None:
        adapter, _ = _armar(store=store)
        await adapter.start()

        await adapter.stop()

        assert store._db is None


class TestBarridoDeArranque:
    """``start()`` levanta lo que dejó un daemon que murió sin ``stop()`` (crash, SIGKILL)."""

    async def test_las_running_y_queued_huerfanas_pasan_a_interrupted(self, store) -> None:
        corriendo = await _sembrar(store, "running")
        en_cola = await _sembrar(store, "queued")
        adapter, dispatcher = _armar(store=store)

        await adapter.start()
        a, b = await store.get(corriendo, "inaki"), await store.get(en_cola, "inaki")
        await adapter.stop()

        for fila in (a, b):
            assert fila.status == "interrupted"
            assert fila.notified is False
            assert fila.failure.kind == "interrupted"  # type: ignore[union-attr]
            assert fila.failure.detail == _DETALLE_ARRANQUE  # type: ignore[union-attr]
            assert fila.finished_at is not None
        dispatcher.dispatch.assert_not_called()

    async def test_el_barrido_recupera_el_ultimo_paso_del_transcript_persistido(
        self, store
    ) -> None:
        """REQ-BGP-2 S1 + REQ-BGP-6: tras el SIGKILL, los pasos ya escritos cuentan."""
        task_id = await _sembrar(store, "running")
        rec = store.transcript(task_id)
        await rec.record(Message(role=Role.USER, content="tarea"))
        await rec.record(
            Message(
                role=Role.ASSISTANT,
                content="",
                tool_calls=[{"id": "c1", "function": {"name": "web_fetch", "arguments": "{}"}}],
            )
        )
        adapter, _ = _armar(store=store)

        await adapter.start()
        fila = await store.get(task_id, "inaki")
        await adapter.stop()

        assert fila.failure.last_step == "called web_fetch"  # type: ignore[union-attr]
        assert fila.failure.in_flight_tool.name == "web_fetch"  # type: ignore[union-attr]
        assert fila.iterations_used == 1

    async def test_delivering_huerfana_pasa_a_delivery_failed_con_su_resultado(self, store) -> None:
        task_id = await _sembrar(store, "delivering", result="[bg-x] success: listo")
        adapter, dispatcher = _armar(store=store)

        await adapter.start()
        fila = await store.get(task_id, "inaki")
        await adapter.stop()

        assert fila.status == "delivery_failed"
        assert fila.result == "[bg-x] success: listo"
        assert fila.error == _ENTREGA_CORTADA
        assert fila.notified is False
        dispatcher.dispatch.assert_not_called()  # no se re-dispatcha sola

    async def test_la_delivery_failed_barrida_se_descarta_con_cancel_y_devuelve_el_resultado(
        self, store
    ) -> None:
        """REQ-BGD-10 S1: el padre recupera el resultado con ``cancel`` → ``dismissed``."""
        task_id = await _sembrar(store, "delivering", result="[bg-x] success: listo")
        adapter, _ = _armar(store=store)
        await adapter.start()

        res = await adapter.cancel(task_id, "inaki")
        fila = await store.get(task_id, "inaki")
        await adapter.stop()

        assert res.outcome == "dismissed"
        assert res.result == "[bg-x] success: listo"
        assert res.error == _ENTREGA_CORTADA
        assert fila.status == "dismissed"

    async def test_las_barridas_quedan_visibles_para_el_padre_tras_el_arranque(self, store) -> None:
        interrumpida = await _sembrar(store, "running")
        entrega = await _sembrar(store, "delivering", result="[bg-x] ok")
        ajena = await _sembrar(store, "running", caller="otro")
        adapter, _ = _armar(store=store)

        await adapter.start()
        vistas = {v.id: v.status for v in adapter.snapshot_inflight("inaki")}
        ajenas = {v.id: v.status for v in adapter.snapshot_inflight("otro")}
        await adapter.stop()

        assert vistas == {interrumpida: "interrupted", entrega: "delivery_failed"}
        assert ajenas == {ajena: "interrupted"}

    async def test_las_terminales_y_las_ya_avisadas_no_aparecen_en_el_snapshot(self, store) -> None:
        """REQ-BGD-9: solo viaja al prompt lo que sigue siendo asunto pendiente del padre."""
        await _sembrar(store, "delivered", finished_at=datetime.now(timezone.utc))
        await _sembrar(store, "cancelled", finished_at=datetime.now(timezone.utc))
        await _sembrar(store, "dismissed", finished_at=datetime.now(timezone.utc))
        await _sembrar(store, "interrupted", notified=True)
        pendiente = await _sembrar(store, "interrupted", notified=False)
        adapter, _ = _armar(store=store)

        await adapter.start()
        vistas = [v.id for v in adapter.snapshot_inflight("inaki")]
        await adapter.stop()

        assert vistas == [pendiente]

    async def test_un_segundo_arranque_no_vuelve_a_barrer_lo_ya_interrumpido(self, store) -> None:
        task_id = await _sembrar(store, "running")
        primero, _ = _armar(store=store)
        await primero.start()
        antes = await store.get(task_id, "inaki")
        await primero.stop()
        await store.open()

        segundo, _ = _armar(store=store)
        await segundo.start()
        despues = await store.get(task_id, "inaki")
        await segundo.stop()

        assert despues.status == "interrupted"
        assert despues.failure == antes.failure  # no se reescribió con otro motivo/fecha
        assert despues.finished_at == antes.finished_at

    async def test_las_terminadas_no_se_barren(self, store) -> None:
        entregada = await _sembrar(
            store, "delivered", result="[bg-x] ok", finished_at=datetime.now(timezone.utc)
        )
        adapter, _ = _armar(store=store)

        await adapter.start()
        fila = await store.get(entregada, "inaki")
        await adapter.stop()

        assert fila.status == "delivered" and fila.notified is True

    async def test_si_el_barrido_falla_loguea_error_y_el_arranque_continua(
        self, tmp_path: Path, caplog
    ) -> None:
        caplog.set_level(logging.ERROR, logger=_LOGGER)
        roto = StoreRoto(tmp_path / "bg.db", fallar=("list_by_status",))
        adapter, dispatcher = _armar(store=roto, one_shot_for={"r": _one_shot(_JSON_OK)})

        await adapter.start()
        await _encolar(adapter)
        await asyncio.sleep(0.15)
        await adapter.stop()

        dispatcher.dispatch.assert_awaited_once()  # la cola arrancó y entregó igual
        errores = [r.getMessage() for r in _errores(caplog)]
        assert any(_CAPACIDAD in e and "barrido" in e for e in errores)

    async def test_el_padre_puede_descartar_una_interrumpida_con_cancel(self, store) -> None:
        """REQ-BGD-9: queda visible hasta que se avisa o la descarta con cancel."""
        task_id = await _sembrar(store, "running")
        adapter, _ = _armar(store=store)
        await adapter.start()

        res = await adapter.cancel(task_id, "inaki")
        fila = await store.get(task_id, "inaki")
        visibles = adapter.snapshot_inflight("inaki")
        await adapter.stop()

        assert (res.outcome, res.status, res.result) == ("dismissed", "interrupted", None)
        assert fila.status == "dismissed" and fila.notified is True
        assert visibles == []


# ---------------------------------------------------------------------------
# 4.11 — poda de workdirs y barrido de huérfanos en start() (REQ-BGW-6/7/8)
# ---------------------------------------------------------------------------

_VIEJA = datetime.now(timezone.utc) - timedelta(days=30)


async def _sembrar_con_workdir(store, workdir: str | None, estado: str = "delivered", **extra):
    """Fila terminada hace 30 días (le toca la poda) que referencia ``workdir``."""
    await store.open()
    fila = _fila_nueva()
    fila.workdir = workdir
    task_id = await store.create(fila)
    await store.update(task_id, status=estado, finished_at=_VIEJA, **extra)
    return task_id


class TestPodaYBarridoDeWorkdirs:
    async def test_la_poda_borra_la_fila_y_DESPUES_el_directorio(self, store, raiz: Path) -> None:
        """REQ-BGW-6 S1: un crash entre ambos deja un dir sin fila (lo barre el arranque),
        nunca una fila apuntando a nada."""
        gestor = _gestor(raiz)
        ruta = _crear(gestor)
        task_id = await _sembrar_con_workdir(store, str(ruta))
        (ruta / "informe.md").write_text("resultado")
        orden: list[str] = []
        prune_real, borrar_real = store.prune, gestor.borrar

        async def prune_espiado(now):
            res = await prune_real(now)
            orden.append("prune")
            return res

        def borrar_espiado(tid, r):
            orden.append("borrar")
            borrar_real(tid, r)

        store.prune = prune_espiado  # type: ignore[method-assign]
        gestor.borrar = borrar_espiado  # type: ignore[method-assign]
        adapter, _ = _armar(store=store, workdirs=gestor)

        await adapter.start()
        fila = await store.get(task_id, "inaki")
        await adapter.stop()

        assert orden == ["prune", "borrar"]
        assert fila is None
        assert not ruta.exists()

    async def test_una_ruta_que_no_pasa_la_compuerta_se_poda_sin_borrar_el_directorio(
        self, store, raiz: Path, caplog
    ) -> None:
        """REQ-BGW-6 S2: la fila cae, ``/…/notas`` queda intacto y el ERROR nombra la ruta."""
        caplog.set_level(logging.ERROR)
        notas = raiz / "notas"
        notas.mkdir()
        (notas / "mio.txt").write_text("no me borres")
        task_id = await _sembrar_con_workdir(store, str(notas))
        adapter, _ = _armar(store=store, workdirs=_gestor(raiz))

        await adapter.start()
        fila = await store.get(task_id, "inaki")
        await adapter.stop()

        assert fila is None
        assert (notas / "mio.txt").read_text() == "no me borres"
        assert any(
            str(notas) in r.getMessage() and r.levelno == logging.ERROR for r in caplog.records
        )

    async def test_un_directorio_sin_fila_se_barre_y_uno_referenciado_no(
        self, store, raiz: Path
    ) -> None:
        """REQ-BGW-7 S1: el crash entre borrar la fila y el rmtree se cura en el arranque."""
        gestor = _gestor(raiz)
        huerfano = _crear(gestor)
        vivo = _crear(gestor)
        await store.open()
        fila = _fila_nueva()
        fila.workdir = str(vivo)
        await store.create(fila)  # queued, reciente: ni se poda ni se barre su dir
        adapter, _ = _armar(store=store, workdirs=gestor)

        await adapter.start()
        await adapter.stop()

        assert not huerfano.exists()
        assert vivo.is_dir()

    async def test_lo_que_no_cumple_el_patron_no_se_toca(self, store, raiz: Path) -> None:
        """REQ-BGW-7 S2."""
        mio = raiz / "dwss" / "mis-notas"
        mio.mkdir(parents=True)
        (mio / "x.txt").write_text("mio")
        adapter, _ = _armar(store=store, workdirs=_gestor(raiz))

        await adapter.start()
        await adapter.stop()

        assert (mio / "x.txt").read_text() == "mio"

    async def test_un_directorio_en_creacion_no_se_barre(self, store, raiz: Path) -> None:
        gestor = _gestor(raiz)
        recien_creado = _crear(gestor)
        adapter, _ = _armar(store=store, workdirs=gestor)
        adapter._en_creacion.add(str(recien_creado))  # su fila todavía no existe

        await adapter.start()
        await adapter.stop()

        assert recien_creado.is_dir()

    async def test_la_poda_borra_en_la_ubicacion_original_aunque_el_workspace_haya_cambiado(
        self, store, tmp_path: Path
    ) -> None:
        """REQ-BGW-8 S1/S2: la fila guarda la ruta ABSOLUTA; el barrido solo mira la raíz actual."""
        w1, w2 = tmp_path / "w1", tmp_path / "w2"
        w1.mkdir()
        w2.mkdir()
        viejo = GestorDeWorkdirs(workspaces={"inaki": w1}, con_workdir=["inaki"])
        podable = _crear(viejo)
        huerfano_en_w1 = _crear(viejo)  # sin fila, bajo la raíz VIEJA
        task_id = await _sembrar_con_workdir(store, str(podable))
        nuevo = GestorDeWorkdirs(workspaces={"inaki": w2}, con_workdir=["inaki"])
        adapter, _ = _armar(store=store, workdirs=nuevo)

        await adapter.start()
        fila = await store.get(task_id, "inaki")
        await adapter.stop()

        assert fila is None
        assert not podable.exists()  # se borró en W1 aunque el caller ya use W2
        assert huerfano_en_w1.is_dir()  # riesgo aceptado: W1 ya no es una raíz conocida

    async def test_con_el_store_caido_no_se_borra_nada_y_se_loguea(
        self, tmp_path: Path, raiz: Path, caplog
    ) -> None:
        """REQ-BGW-7 S3: sin store no se sabe qué está referenciado → no se barre."""
        caplog.set_level(logging.WARNING, logger=_LOGGER)
        gestor = _gestor(raiz)
        sin_fila = _crear(gestor)
        adapter, _ = _armar(store=StoreRoto(tmp_path / "bg.db", fallar=("open",)), workdirs=gestor)

        await adapter.start()
        await adapter.stop()

        assert sin_fila.is_dir()
        assert any("no se barren workdirs huérfanos" in r.getMessage() for r in caplog.records)

    async def test_sin_store_inyectado_tampoco_se_barre(self, raiz: Path, caplog) -> None:
        caplog.set_level(logging.WARNING, logger=_LOGGER)
        gestor = _gestor(raiz)
        sin_fila = _crear(gestor)
        adapter, _ = _armar(store=None, workdirs=gestor)

        await adapter.start()
        await adapter.stop()

        assert sin_fila.is_dir()
        assert any("no se barren workdirs huérfanos" in r.getMessage() for r in caplog.records)

    async def test_sin_gestor_no_hay_nada_que_barrer_ni_que_avisar(self, store, caplog) -> None:
        caplog.set_level(logging.WARNING, logger=_LOGGER)
        adapter, _ = _armar(store=store, workdirs=None)

        await adapter.start()
        await adapter.stop()

        assert not [r for r in caplog.records if "workdirs" in r.getMessage()]

    async def test_un_fallo_de_la_poda_o_del_borrado_nunca_tumba_el_arranque(
        self, tmp_path: Path, raiz: Path, caplog
    ) -> None:
        caplog.set_level(logging.ERROR, logger=_LOGGER)
        roto = StoreRoto(tmp_path / "bg.db", fallar=("prune",))
        gestor = _gestor(raiz)
        huerfano = _crear(gestor)
        adapter, dispatcher = _armar(
            store=roto, one_shot_for={"r": _one_shot(_JSON_OK)}, workdirs=gestor
        )

        await adapter.start()
        await _encolar(adapter)
        await asyncio.sleep(0.15)
        await adapter.stop()

        dispatcher.dispatch.assert_awaited_once()  # la cola arrancó y entregó igual
        assert any(
            _CAPACIDAD in r.getMessage() and "poda" in r.getMessage() for r in caplog.records
        )
        assert not huerfano.exists()  # el barrido corrió aunque la poda falló

    async def test_si_el_barrido_explota_el_arranque_continua(
        self, store, raiz: Path, monkeypatch, caplog
    ) -> None:
        caplog.set_level(logging.ERROR, logger=_LOGGER)
        gestor = _gestor(raiz)

        def explota(*_a, **_k):
            raise RuntimeError("fs roto")

        gestor.barrer = explota  # type: ignore[method-assign]
        adapter, dispatcher = _armar(
            store=store, one_shot_for={"r": _one_shot(_JSON_OK)}, workdirs=gestor
        )

        await adapter.start()
        await _encolar(adapter)
        await asyncio.sleep(0.15)
        await adapter.stop()

        dispatcher.dispatch.assert_awaited_once()
        assert any("barrido de workdirs" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 4.7 — aviso único tras el arranque (REQ-BGD-9/11)
# ---------------------------------------------------------------------------


async def _esperar_aviso(adapter) -> None:
    """Espera a que termine la tarea de aviso (la que programa ``programar_avisos_post_arranque``)."""
    tarea = adapter._aviso_task
    assert tarea is not None, "no se programó ningún aviso"
    await asyncio.wait_for(asyncio.shield(tarea), timeout=5)


async def _avisar(adapter) -> None:
    adapter.programar_avisos_post_arranque()
    await _esperar_aviso(adapter)


class TestAvisoPostArranque:
    async def test_programar_devuelve_enseguida_sin_esperar_al_dispatch(self, store) -> None:
        await _sembrar(store, "running")
        liberar = asyncio.Event()
        dispatcher = MagicMock()

        async def lento(**_kw):
            await liberar.wait()
            return ""

        dispatcher.dispatch = AsyncMock(side_effect=lento)
        adapter, _ = _armar(store=store, dispatcher=dispatcher)
        await adapter.start()

        adapter.programar_avisos_post_arranque()  # sync: no bloquea el arranque
        await asyncio.sleep(0.05)
        assert adapter._aviso_task is not None
        todavia_corriendo = not adapter._aviso_task.done()
        liberar.set()
        await _esperar_aviso(adapter)
        await adapter.stop()

        assert todavia_corriendo

    async def test_un_solo_dispatch_por_conversacion_con_todas_las_interrumpidas(
        self, store
    ) -> None:
        uno = await _sembrar(store, "running", prompt="tarea uno")
        dos = await _sembrar(store, "queued", prompt="tarea dos")
        adapter, dispatcher = _armar(store=store)
        await adapter.start()

        await _avisar(adapter)
        await adapter.stop()

        dispatcher.dispatch.assert_awaited_once()
        kw = dispatcher.dispatch.await_args.kwargs
        assert kw["agent_id"] == "inaki"
        assert (kw["channel"], kw["chat_id"]) == ("telegram", "42")
        assert uno in kw["prompt"] and dos in kw["prompt"]
        assert "interrupted" in kw["prompt"]

    async def test_el_aviso_no_rutea_por_su_texto(self, store) -> None:
        """El aviso del sistema tampoco es intención del usuario: no se embebe."""
        await _sembrar(store, "running")
        adapter, dispatcher = _armar(store=store)
        await adapter.start()

        await _avisar(adapter)
        await adapter.stop()

        assert dispatcher.dispatch.await_args.kwargs["skip_routing"] is True

    async def test_una_conversacion_distinta_recibe_su_propio_aviso(self, store) -> None:
        await _sembrar(store, "running", chat_id="42")
        await _sembrar(store, "running", chat_id="77")
        await _sembrar(store, "running", caller="otro", chat_id="42")
        adapter, dispatcher = _armar(store=store)
        await adapter.start()

        await _avisar(adapter)
        await adapter.stop()

        destinos = {
            (c.kwargs["agent_id"], c.kwargs["chat_id"]) for c in dispatcher.dispatch.await_args_list
        }
        assert destinos == {("inaki", "42"), ("inaki", "77"), ("otro", "42")}

    async def test_las_entregas_barridas_van_en_otra_linea_y_se_recuperan_con_cancel(
        self, store
    ) -> None:
        interrumpida = await _sembrar(store, "running")
        entrega = await _sembrar(store, "delivering", result="[bg-x] ok")
        adapter, dispatcher = _armar(store=store)
        await adapter.start()

        await _avisar(adapter)
        await adapter.stop()

        prompt = dispatcher.dispatch.await_args.kwargs["prompt"]
        assert interrumpida in prompt and entrega in prompt
        assert "result not delivered" in prompt
        assert f"background_tasks(action=cancel, task_id={entrega})" in prompt

    async def test_las_entregas_fallidas_en_runtime_no_se_vuelven_a_avisar(self, store) -> None:
        """Una ``delivery_failed`` normal (notified=True) ya se la ve en el prompt: no es aviso."""
        await _sembrar(store, "delivery_failed", result="[bg-x] ok")
        adapter, dispatcher = _armar(store=store)
        await adapter.start()

        adapter.programar_avisos_post_arranque()
        await asyncio.sleep(0.05)
        await adapter.stop()

        dispatcher.dispatch.assert_not_awaited()

    async def test_sin_nada_pendiente_no_se_manda_nada(self, store) -> None:
        adapter, dispatcher = _armar(store=store)
        await adapter.start()

        adapter.programar_avisos_post_arranque()
        await asyncio.sleep(0.05)
        await adapter.stop()

        dispatcher.dispatch.assert_not_awaited()

    async def test_notified_se_marca_solo_tras_un_dispatch_exitoso_y_no_se_repite(
        self, store
    ) -> None:
        task_id = await _sembrar(store, "running")
        adapter, dispatcher = _armar(store=store)
        await adapter.start()

        await _avisar(adapter)
        fila = await store.get(task_id, "inaki")
        visibles = adapter.snapshot_inflight("inaki")
        await adapter.stop()
        otro, dispatcher2 = _armar(store=store)
        await otro.start()
        otro.programar_avisos_post_arranque()
        await asyncio.sleep(0.05)
        await otro.stop()

        assert fila.notified is True
        assert visibles == []  # avisada: deja de ser asunto pendiente
        dispatcher2.dispatch.assert_not_awaited()  # ni en el siguiente arranque

    async def test_la_marca_llega_ANTES_de_entregar_la_respuesta_al_canal(self, store) -> None:
        task_id = await _sembrar(store, "running")
        sender = MagicMock()
        sender.is_conversational.return_value = True
        sender.build_intermediate_sink.return_value = None
        vista: dict = {}

        async def enviar(*_a, **_k):
            vista["notified"] = (await store.get(task_id, "inaki")).notified

        sender.send_message = AsyncMock(side_effect=enviar)
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="avisé al usuario")
        adapter = BackgroundDelegationQueueAdapter(
            dispatcher=dispatcher,
            one_shot_resolver=lambda c, t: None,
            max_concurrent=1,
            store=store,
            result_sender=sender,
        )
        await adapter.start()

        await _avisar(adapter)
        await adapter.stop()

        assert vista == {"notified": True}

    async def test_si_el_dispatch_falla_no_se_marca_y_se_reintenta_en_el_proximo_arranque(
        self, store, monkeypatch, caplog
    ) -> None:
        caplog.set_level(logging.WARNING, logger=_LOGGER)
        monkeypatch.setattr("inaki.agents.delegation.background_queue._DISPATCH_RETRY_DELAY", 0)
        task_id = await _sembrar(store, "running")
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(side_effect=RuntimeError("provider caído"))
        adapter, _ = _armar(store=store, dispatcher=dispatcher)
        await adapter.start()

        await _avisar(adapter)
        fila = await store.get(task_id, "inaki")
        visibles = adapter.snapshot_inflight("inaki")
        await adapter.stop()

        assert dispatcher.dispatch.await_count == 3
        assert fila.notified is False
        assert [v.id for v in visibles] == [task_id]  # sigue visible para el padre
        assert any("no se pudo avisar" in r.getMessage() for r in caplog.records)

    async def test_un_agente_que_ya_no_existe_corta_sin_reintentos(self, store, caplog) -> None:
        caplog.set_level(logging.WARNING, logger=_LOGGER)
        task_id = await _sembrar(store, "running", caller="borrado")
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(side_effect=AgentNotFoundError("borrado"))
        adapter, _ = _armar(store=store, dispatcher=dispatcher)
        await adapter.start()

        await _avisar(adapter)
        fila = await store.get(task_id, "borrado")
        await adapter.stop()

        assert dispatcher.dispatch.await_count == 1
        assert fila.notified is False
        assert any("borrado" in r.getMessage() for r in caplog.records)

    async def test_un_fallo_del_dispatch_de_una_conversacion_no_frena_a_las_demas(
        self, store, monkeypatch
    ) -> None:
        monkeypatch.setattr("inaki.agents.delegation.background_queue._DISPATCH_RETRY_DELAY", 0)
        mala = await _sembrar(store, "running", chat_id="1")
        buena = await _sembrar(store, "running", chat_id="2")
        dispatcher = MagicMock()

        async def segun_chat(**kw):
            if kw["chat_id"] == "1":
                raise RuntimeError("x")
            return ""

        dispatcher.dispatch = AsyncMock(side_effect=segun_chat)
        adapter, _ = _armar(store=store, dispatcher=dispatcher)
        await adapter.start()

        await _avisar(adapter)
        f_mala = await store.get(mala, "inaki")
        f_buena = await store.get(buena, "inaki")
        await adapter.stop()

        assert (f_mala.notified, f_buena.notified) == (False, True)

    async def test_si_marcar_notified_falla_se_loguea_la_capacidad_perdida(
        self, tmp_path: Path, caplog
    ) -> None:
        caplog.set_level(logging.ERROR, logger=_LOGGER)
        roto = StoreRoto(tmp_path / "bg.db", fallar=("mark_notified",))
        await _sembrar(roto, "running")
        adapter, dispatcher = _armar(store=roto)
        await adapter.start()

        await _avisar(adapter)
        await adapter.stop()

        dispatcher.dispatch.assert_awaited_once()
        assert any(_CAPACIDAD in r.getMessage() for r in caplog.records)

    async def test_stop_corta_un_aviso_en_vuelo(self, store) -> None:
        await _sembrar(store, "running")
        dispatcher = MagicMock()

        async def eterno(**_k):
            await asyncio.sleep(60)

        dispatcher.dispatch = AsyncMock(side_effect=eterno)
        adapter, _ = _armar(store=store, dispatcher=dispatcher)
        await adapter.start()
        adapter.programar_avisos_post_arranque()
        await asyncio.sleep(0.05)
        tarea = adapter._aviso_task
        assert tarea is not None

        await asyncio.wait_for(adapter.stop(), timeout=5)

        assert tarea.done()

    async def test_sin_store_no_hay_nada_que_avisar(self) -> None:
        adapter, dispatcher = _armar(store=None)
        await adapter.start()

        adapter.programar_avisos_post_arranque()
        await asyncio.sleep(0.05)
        await adapter.stop()

        dispatcher.dispatch.assert_not_awaited()

    async def test_programar_dos_veces_no_duplica_el_aviso(self, store) -> None:
        await _sembrar(store, "running")
        adapter, dispatcher = _armar(store=store)
        await adapter.start()

        adapter.programar_avisos_post_arranque()
        adapter.programar_avisos_post_arranque()
        await _esperar_aviso(adapter)
        await adapter.stop()

        dispatcher.dispatch.assert_awaited_once()


def _armar_con_sender(
    store, *, conversacional=lambda canal, agente: True
) -> tuple[BackgroundDelegationQueueAdapter, MagicMock]:
    """Cola con un ``result_sender`` que decide qué scopes son un canal conversacional vivo."""
    sender = MagicMock()
    sender.is_conversational.side_effect = conversacional
    sender.build_intermediate_sink.return_value = None
    sender.send_message = AsyncMock()
    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock(return_value="")
    adapter = BackgroundDelegationQueueAdapter(
        dispatcher=dispatcher,
        one_shot_resolver=lambda c, t: None,
        max_concurrent=1,
        store=store,
        result_sender=sender,
    )
    return adapter, dispatcher


class TestAvisoSinCanalAlQueAvisar:
    """REQ-BGD-11 S4: un scope sin canal conversacional no recibe un turno de aviso.

    Ese turno caería en un historial huérfano y nadie lo leería: la task se queda visible en
    la sección in-flight del padre hasta que la descarte con ``cancel`` o la retención la
    pode, sin marcarla ``notified``.
    """

    async def test_un_scope_vacio_no_dispara_turno_y_la_task_sigue_visible(
        self, store, caplog
    ) -> None:
        caplog.set_level(logging.INFO, logger=_LOGGER)
        task_id = await _sembrar(store, "running", chat_id="")
        adapter, dispatcher = _armar_con_sender(store)
        await adapter.start()

        await _avisar(adapter)
        fila = await store.get(task_id, "inaki")
        visibles = adapter.snapshot_inflight("inaki")
        await adapter.stop()

        dispatcher.dispatch.assert_not_awaited()
        assert fila.notified is False
        assert [v.id for v in visibles] == [task_id]
        assert [v.status for v in visibles] == ["interrupted"]
        info = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert any(task_id in m and "in-flight" in m for m in info)

    async def test_un_canal_no_conversacional_para_ese_caller_tampoco_dispara_turno(
        self, store
    ) -> None:
        task_id = await _sembrar(store, "running", caller="otro")
        adapter, dispatcher = _armar_con_sender(
            store, conversacional=lambda canal, agente: agente != "otro"
        )
        await adapter.start()

        await _avisar(adapter)
        fila = await store.get(task_id, "otro")
        visibles = adapter.snapshot_inflight("otro")
        await adapter.stop()

        dispatcher.dispatch.assert_not_awaited()
        assert fila.notified is False
        assert [v.id for v in visibles] == [task_id]

    async def test_con_scopes_mezclados_solo_se_avisa_al_conversacional(self, store) -> None:
        sin_canal = await _sembrar(store, "running", chat_id="")
        con_canal = await _sembrar(store, "running", chat_id="42")
        adapter, dispatcher = _armar_con_sender(store)
        await adapter.start()

        await _avisar(adapter)
        f_sin = await store.get(sin_canal, "inaki")
        f_con = await store.get(con_canal, "inaki")
        visibles = {v.id for v in adapter.snapshot_inflight("inaki")}
        await adapter.stop()

        dispatcher.dispatch.assert_awaited_once()
        kw = dispatcher.dispatch.await_args.kwargs
        assert kw["chat_id"] == "42"
        assert con_canal in kw["prompt"] and sin_canal not in kw["prompt"]
        assert (f_sin.notified, f_con.notified) == (False, True)
        assert visibles == {sin_canal}  # la avisada sale; la que no tenía a quién, queda

    async def test_una_entrega_barrida_sin_canal_tampoco_se_avisa_y_sigue_cancelable(
        self, store
    ) -> None:
        task_id = await _sembrar(store, "delivering", chat_id="", result="[bg-x] ok")
        adapter, dispatcher = _armar_con_sender(store)
        await adapter.start()

        await _avisar(adapter)
        fila = await store.get(task_id, "inaki")
        visibles = [v.id for v in adapter.snapshot_inflight("inaki")]
        salida = await adapter.cancel(task_id, "inaki")
        await adapter.stop()

        dispatcher.dispatch.assert_not_awaited()
        assert fila.status == "delivery_failed" and fila.notified is False
        assert visibles == [task_id]
        assert salida.outcome == "dismissed" and salida.result == "[bg-x] ok"

    async def test_el_padre_descarta_una_interrumpida_sin_aviso_con_cancel(self, store) -> None:
        task_id = await _sembrar(store, "running", chat_id="")
        adapter, _ = _armar_con_sender(store)
        await adapter.start()
        await _avisar(adapter)

        salida = await adapter.cancel(task_id, "inaki")
        visibles = adapter.snapshot_inflight("inaki")
        await adapter.stop()

        assert salida.outcome == "dismissed" and salida.status == "interrupted"
        assert visibles == []


class TestPodaAntesDeRehidratar:
    """Design D8: podar → rehidratar. Lo podado no puede reaparecer en el dict."""

    async def test_una_interrumpida_sin_avisar_de_mas_de_14_dias_se_poda_y_no_se_rehidrata(
        self, store
    ) -> None:
        vieja = await _sembrar(
            store,
            "interrupted",
            finished_at=datetime.now(timezone.utc) - timedelta(days=20),
            notified=False,
        )
        adapter, dispatcher = _armar(store=store)
        await adapter.start()

        visibles = adapter.snapshot_inflight("inaki")
        en_dict = vieja in adapter._tasks
        adapter.programar_avisos_post_arranque()
        await _esperar_aviso(adapter)
        fila = await store.get(vieja, "inaki")
        await adapter.stop()

        assert fila is None
        assert visibles == [] and en_dict is False
        dispatcher.dispatch.assert_not_awaited()


class TestRedDeSeguridadDelCicloDeVida:
    """Clase ``bg-stuck-task``: una excepción inesperada fuera del try del hijo no deja a la
    task en ``running``/``delivering`` para siempre ni mata al consumer."""

    @staticmethod
    def _con_sender_que_falla_una_vez(store):
        sender = MagicMock()
        sender.is_conversational.return_value = True
        sender.build_intermediate_sink.side_effect = [RuntimeError("sink roto"), None, None]
        sender.send_message = AsyncMock()
        return sender

    @staticmethod
    def _cola(store, sender, dispatcher):
        return BackgroundDelegationQueueAdapter(
            dispatcher=dispatcher,
            one_shot_resolver=lambda c, t: _one_shot(_JSON_OK),
            max_concurrent=1,
            store=store,
            result_sender=sender,
        )

    @staticmethod
    async def _esperar_a_que_termine(adapter, task_id: str) -> None:
        handle = adapter._handles.get(task_id)
        if handle is not None:
            await asyncio.wait_for(asyncio.shield(handle), timeout=5)

    async def test_si_falla_el_sink_tras_persistir_el_resultado_queda_delivery_failed(
        self, store, caplog
    ) -> None:
        caplog.set_level(logging.ERROR, logger=_LOGGER)
        listo = asyncio.Event()

        async def dispatch(**_kw):
            listo.set()
            return ""

        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(side_effect=dispatch)
        adapter = self._cola(store, self._con_sender_que_falla_una_vez(store), dispatcher)
        await adapter.start()

        primera = await _encolar(adapter)
        segunda = await _encolar(adapter)
        await asyncio.wait_for(listo.wait(), timeout=5)
        await self._esperar_a_que_termine(adapter, segunda)
        f1 = await store.get(primera, "inaki")
        f2 = await store.get(segunda, "inaki")
        viva = primera in adapter._tasks
        await adapter.stop()

        # La primera: resultado conservado + error honesto, recuperable con cancel.
        assert f1.status == "delivery_failed"
        assert "Saldo $5.420" in (f1.result or "")
        assert "RuntimeError: sink roto" in (f1.error or "")
        assert viva is True
        # El consumer siguió: la segunda se entregó (y el dispatch no se tocó para la primera).
        assert f2.status == "delivered"
        dispatcher.dispatch.assert_awaited_once()
        logueado = [r for r in caplog.records if r.levelno == logging.ERROR and r.exc_info]
        assert any(primera in r.getMessage() for r in logueado)

    async def test_si_falla_un_paso_posterior_al_hijo_el_padre_recibe_un_fallo_interno(
        self, store, monkeypatch
    ) -> None:
        """Sin resultado todavía (``running``): el reporte interno se persiste y se entrega,
        igual que cualquier otro fallo del hijo; la task no queda atascada."""
        import inaki.agents.delegation.background_queue as modulo

        real = modulo.render_resultado
        llamadas = {"n": 0}

        def render_roto(task_id, raw):
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                raise ValueError("render roto")
            return real(task_id, raw)

        monkeypatch.setattr(modulo, "render_resultado", render_roto)
        prompts: list[str] = []
        listo = asyncio.Event()

        async def dispatch(**kw):
            prompts.append(kw["prompt"])
            if len(prompts) == 2:
                listo.set()
            return ""

        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(side_effect=dispatch)
        adapter, _ = _armar(
            store=store, one_shot_for={"r": _one_shot(_JSON_OK)}, dispatcher=dispatcher
        )
        await adapter.start()

        primera = await _encolar(adapter)
        segunda = await _encolar(adapter)
        await asyncio.wait_for(listo.wait(), timeout=5)
        await self._esperar_a_que_termine(adapter, segunda)
        f1 = await store.get(primera, "inaki")
        f2 = await store.get(segunda, "inaki")
        await adapter.stop()

        assert f1.status == "delivered"
        assert f1.failure is not None and f1.failure.kind == "exception"
        assert f1.result == prompts[0]
        assert prompts[0].startswith(f"[{primera}] failed (exception)")
        assert "ValueError: render roto" in prompts[0]
        assert f2.status == "delivered" and prompts[1].startswith(f"[{segunda}] success")

    async def test_si_tambien_revienta_la_entrega_del_fallo_interno_queda_delivery_failed(
        self, store, monkeypatch
    ) -> None:
        import inaki.agents.delegation.background_queue as modulo

        def render_roto(task_id, raw):
            raise ValueError("render roto")

        monkeypatch.setattr(modulo, "render_resultado", render_roto)
        sender = MagicMock()
        sender.is_conversational.return_value = True
        sender.build_intermediate_sink.side_effect = RuntimeError("sink roto")
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="")
        adapter = self._cola(store, sender, dispatcher)
        await adapter.start()

        task_id = await _encolar(adapter)
        await asyncio.wait_for(asyncio.shield(await self._handle(adapter, task_id)), timeout=5)
        fila = await store.get(task_id, "inaki")
        await adapter.stop()

        assert fila.status == "delivery_failed"
        assert fila.result is not None and "failed (exception)" in fila.result
        assert "RuntimeError: sink roto" in (fila.error or "")
        dispatcher.dispatch.assert_not_awaited()

    @staticmethod
    async def _handle(adapter, task_id: str) -> asyncio.Task:
        """El handle de la task, apenas el consumer lo crea (sin sleeps fijos)."""
        for _ in range(500):
            handle = adapter._handles.get(task_id)
            if handle is not None:
                return handle
            await asyncio.sleep(0)
        raise AssertionError(f"el consumer nunca lanzó {task_id}")

    async def test_cancelar_una_task_en_curso_sigue_cortando_limpio(self, store) -> None:
        """La red de seguridad no atrapa ``CancelledError``: cancel/stop conservan su semántica."""
        arrancado = asyncio.Event()
        hijo = _one_shot()

        async def eterno(**_kw):
            arrancado.set()
            await asyncio.sleep(60)

        hijo.execute = AsyncMock(side_effect=eterno)
        adapter, dispatcher = _armar(store=store, one_shot_for={"r": hijo})
        await adapter.start()

        task_id = await _encolar(adapter)
        await asyncio.wait_for(arrancado.wait(), timeout=5)
        salida = await adapter.cancel(task_id, "inaki")
        fila = await store.get(task_id, "inaki")
        await adapter.stop()

        assert salida.outcome == "cancelled"
        assert fila.status == "cancelled"
        dispatcher.dispatch.assert_not_awaited()


class TestTranscriptIncompleto:
    async def test_se_expone_si_el_transcript_de_una_task_quedo_cortado(
        self, tmp_path: Path
    ) -> None:
        """Seam para ``inspect`` (F5): el flag vive en el recorder, que no sobrevive al hijo."""
        roto = StoreRoto(tmp_path / "bg.db")
        adapter, _ = _armar(store=roto, one_shot_for={"r": _one_shot(_JSON_OK)})
        await adapter.start()
        adapter._recorder_de = lambda task: _RecorderCortado()  # type: ignore[method-assign]

        task_id = await _encolar(adapter)
        await asyncio.sleep(0.15)
        incompleto = adapter.transcript_incompleto(task_id)
        await adapter.stop()

        assert incompleto is True

    async def test_una_task_normal_no_esta_incompleta(self, store) -> None:
        adapter, _ = _armar(store=store, one_shot_for={"r": _one_shot(_JSON_OK)})
        await adapter.start()

        task_id = await _encolar(adapter)
        await asyncio.sleep(0.15)
        incompleto = adapter.transcript_incompleto(task_id)
        await adapter.stop()

        assert incompleto is False


class _RecorderCortado(NullDelegationTranscript):
    incompleto = True


# ---------------------------------------------------------------------------
# 5.1 — inspect en la cola (REQ-BGI-1 / REQ-BGI-2 / REQ-BGI-4) y transcript incompleto persistido
# ---------------------------------------------------------------------------


def _hijo_que_anota(pasos: int, *, retener: asyncio.Event | None = None) -> MagicMock:
    """Hijo que deja ``pasos`` mensajes en su recorder y, si se le pide, se queda esperando."""
    hijo = _one_shot()

    async def corre(**kw):
        rec = kw["recorder"]
        await rec.record(Message(role=Role.USER, content=kw["task"]))
        for i in range(1, pasos):
            await rec.record(Message(role=Role.ASSISTANT, content=f"paso {i}"))
        if retener is not None:
            await retener.wait()
        return _JSON_OK

    hijo.execute = AsyncMock(side_effect=corre)
    return hijo


class TestInspectEnLaCola:
    async def test_una_task_entregada_devuelve_su_estado_y_los_ultimos_pasos(self, store) -> None:
        adapter, _ = _armar(store=store, one_shot_for={"r": _hijo_que_anota(5)})
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.15)

        insp = await adapter.inspect(task_id, "inaki", last_n=3)
        await adapter.stop()

        assert insp is not None
        assert insp.persisted is True
        assert insp.task.id == task_id
        assert insp.task.status == "delivered"
        assert insp.messages_total == 5
        assert [m.seq for m in insp.messages] == [3, 4, 5]
        assert insp.transcript_incomplete is False

    async def test_una_task_running_muestra_los_pasos_hechos_hasta_ahora(self, store) -> None:
        """REQ-BGI-1 S2."""
        retener = asyncio.Event()
        adapter, _ = _armar(store=store, one_shot_for={"r": _hijo_que_anota(3, retener=retener)})
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.1)

        insp = await adapter.inspect(task_id, "inaki", last_n=10)
        retener.set()
        await adapter.stop()

        assert insp is not None
        assert insp.task.status == "running"
        assert insp.messages_total == 3
        assert [m.role for m in insp.messages] == [Role.USER, Role.ASSISTANT, Role.ASSISTANT]

    async def test_una_task_cancelada_en_cola_no_tiene_pasos(self, store) -> None:
        """REQ-BGI-1 S4."""
        adapter, _ = _armar(store=store, one_shot_for={"r": _hijo_lento()}, max_concurrent=1)
        await adapter.start()
        await _encolar(adapter)
        segunda = await _encolar(adapter)
        await asyncio.sleep(0.05)
        await adapter.cancel(segunda, "inaki")

        insp = await adapter.inspect(segunda, "inaki", last_n=10)
        await adapter.stop()

        assert insp is not None
        assert insp.task.status == "cancelled"
        assert insp.messages == []
        assert insp.messages_total == 0
        assert insp.persisted is True

    async def test_el_resultado_y_el_fallo_de_una_task_terminada_viajan_en_la_inspeccion(
        self, store
    ) -> None:
        """REQ-BGI-1 S1: lo que explica POR QUÉ terminó así está en la task inspeccionada."""
        hijo = _one_shot()
        hijo.execute = AsyncMock(side_effect=TimeoutError(""))
        adapter, _ = _armar(store=store, one_shot_for={"r": hijo})
        task_id = await _correr(adapter)

        await adapter.start()
        insp = await adapter.inspect(task_id, "inaki", last_n=10)
        await adapter.stop()

        assert insp is not None
        assert insp.task.failure is not None
        assert insp.task.failure.kind == "timeout"
        assert insp.task.timeout_seconds == 30

    async def test_las_tasks_de_otro_caller_son_not_found_igual_que_una_inexistente(
        self, store
    ) -> None:
        """REQ-BGI-2: no se revela que el id existe."""
        adapter, _ = _armar(store=store, one_shot_for={"r": _hijo_que_anota(2)})
        await adapter.start()
        ajena = await _encolar(adapter, caller="otro")
        await asyncio.sleep(0.15)

        de_otro = await adapter.inspect(ajena, "inaki", last_n=10)
        inexistente = await adapter.inspect("bg-9999", "inaki", last_n=10)
        propia = await adapter.inspect(ajena, "otro", last_n=10)
        await adapter.stop()

        assert de_otro is None
        assert inexistente is None
        assert propia is not None

    async def test_la_task_viva_de_otro_caller_tampoco_se_ve(self, store) -> None:
        retener = asyncio.Event()
        adapter, _ = _armar(store=store, one_shot_for={"r": _hijo_que_anota(2, retener=retener)})
        await adapter.start()
        ajena = await _encolar(adapter, caller="otro")
        await asyncio.sleep(0.05)

        de_otro = await adapter.inspect(ajena, "inaki", last_n=10)
        retener.set()
        await adapter.stop()

        assert de_otro is None

    async def test_una_task_podada_responde_none(self, store) -> None:
        """REQ-BGI-3: la cola dice "no la tengo" (el tool lo redacta sin afirmar inexistencia)."""
        await store.open()
        vieja = await store.create(_fila_nueva())
        await store.update(
            vieja, status="delivered", finished_at=datetime.now(timezone.utc) - timedelta(days=30)
        )
        adapter, _ = _armar(store=store)
        await adapter.start()  # el barrido de arranque la poda

        insp = await adapter.inspect(vieja, "inaki", last_n=10)
        await adapter.stop()

        assert insp is None

    async def test_inspect_repetido_no_cambia_ni_el_estado_ni_notified(self, store) -> None:
        """REQ-BGI-4: es de solo lectura."""
        interrumpida = await _sembrar(
            store, "interrupted", notified=False, finished_at=datetime.now(timezone.utc)
        )
        adapter, dispatcher = _armar(store=store)
        await adapter.start()

        primera = await adapter.inspect(interrumpida, "inaki", last_n=10)
        segunda = await adapter.inspect(interrumpida, "inaki", last_n=10)
        fila = await store.get(interrumpida, "inaki")
        en_el_dict = adapter.snapshot_inflight("inaki")
        await adapter.stop()

        assert primera is not None and segunda is not None
        assert (fila.status, fila.notified) == ("interrupted", False)  # type: ignore[union-attr]
        assert [v.status for v in en_el_dict] == ["interrupted"]
        dispatcher.dispatch.assert_not_awaited()

    async def test_last_n_cero_trae_el_total_sin_pasos(self, store) -> None:
        adapter, _ = _armar(store=store, one_shot_for={"r": _hijo_que_anota(4)})
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.15)

        insp = await adapter.inspect(task_id, "inaki", last_n=0)
        await adapter.stop()

        assert insp is not None
        assert (insp.messages, insp.messages_total) == ([], 4)


class TestInspectSinPersistencia:
    async def test_una_bg_m_viva_se_inspecciona_pero_no_tiene_transcript(self) -> None:
        retener = asyncio.Event()
        adapter, _ = _armar(store=None, one_shot_for={"r": _hijo_que_anota(2, retener=retener)})
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.05)

        insp = await adapter.inspect(task_id, "inaki", last_n=10)
        retener.set()
        await adapter.stop()

        assert task_id == "bg-m1"
        assert insp is not None
        assert insp.persisted is False
        assert insp.messages == []
        assert insp.task.status == "running"

    async def test_una_bg_m_que_ya_no_esta_viva_responde_none(self) -> None:
        adapter, _ = _armar(store=None, one_shot_for={"r": _one_shot(_JSON_OK)})
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.15)

        insp = await adapter.inspect(task_id, "inaki", last_n=10)
        await adapter.stop()

        assert insp is None

    async def test_una_bg_m_de_otro_caller_responde_none(self) -> None:
        retener = asyncio.Event()
        adapter, _ = _armar(store=None, one_shot_for={"r": _hijo_que_anota(1, retener=retener)})
        await adapter.start()
        task_id = await _encolar(adapter, caller="otro")
        await asyncio.sleep(0.05)

        insp = await adapter.inspect(task_id, "inaki", last_n=10)
        retener.set()
        await adapter.stop()

        assert insp is None

    async def test_si_leer_el_store_falla_se_degrada_ruidosamente_a_lo_que_hay_en_memoria(
        self, tmp_path: Path, caplog
    ) -> None:
        caplog.set_level(logging.ERROR, logger=_LOGGER)
        retener = asyncio.Event()
        roto = StoreRoto(tmp_path / "bg.db")
        adapter, _ = _armar(store=roto, one_shot_for={"r": _hijo_que_anota(2, retener=retener)})
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.05)

        async def get_roto(*_a, **_k):
            raise sqlite3.OperationalError("disk I/O error (get)")

        roto.get = get_roto  # type: ignore[method-assign]
        insp = await adapter.inspect(task_id, "inaki", last_n=10)
        retener.set()
        await adapter.stop()

        assert insp is not None
        assert insp.persisted is False
        assert insp.task.status == "running"
        assert any(_CAPACIDAD in r.getMessage() for r in _errores(caplog))


class TestTranscriptIncompletoPersistido:
    """El flag vive en la fila: tiene que sobrevivir a un reinicio (batch F4 lo dejó solo
    en memoria, y se perdía con el proceso)."""

    async def test_el_flag_se_escribe_en_la_fila_al_cortarse_el_transcript(self, store) -> None:
        adapter, _ = _armar(store=store, one_shot_for={"r": _one_shot(_JSON_OK)})
        await adapter.start()
        adapter._recorder_de = lambda task: _RecorderCortado()  # type: ignore[method-assign]

        task_id = await _encolar(adapter)
        await asyncio.sleep(0.15)
        fila = await store.get(task_id, "inaki")
        await adapter.stop()

        assert fila is not None
        assert fila.transcript_incomplete is True

    async def test_sobrevive_a_un_reinicio_y_inspect_lo_declara(self, tmp_path: Path) -> None:
        ruta = tmp_path / "bg.db"
        primero = SQLiteBackgroundTaskStore(ruta)
        adapter, _ = _armar(store=primero, one_shot_for={"r": _one_shot(_JSON_OK)})
        await adapter.start()
        adapter._recorder_de = lambda task: _RecorderCortado()  # type: ignore[method-assign]
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.15)
        await adapter.stop()

        segundo = SQLiteBackgroundTaskStore(ruta)
        nuevo, _ = _armar(store=segundo)
        await nuevo.start()
        insp = await nuevo.inspect(task_id, "inaki", last_n=10)
        await nuevo.stop()

        assert insp is not None
        assert insp.transcript_incomplete is True

    async def test_una_task_normal_no_se_marca_incompleta(self, store) -> None:
        adapter, _ = _armar(store=store, one_shot_for={"r": _hijo_que_anota(3)})
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.15)

        insp = await adapter.inspect(task_id, "inaki", last_n=10)
        await adapter.stop()

        assert insp is not None
        assert insp.transcript_incomplete is False

    async def test_una_task_cuya_persistencia_se_rompio_se_declara_incompleta(
        self, tmp_path: Path
    ) -> None:
        """Si ni el UPDATE puede escribir el flag, ``inspect`` igual lo sabe por la memoria."""
        retener = asyncio.Event()
        roto = StoreRoto(tmp_path / "bg.db")
        adapter, _ = _armar(store=roto, one_shot_for={"r": _hijo_que_anota(2, retener=retener)})
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.05)
        roto.fallar.add("update")
        await adapter.cancel(task_id, "inaki")  # el UPDATE falla → _sin_persistir
        roto.fallar.clear()

        insp = await adapter.inspect(task_id, "inaki", last_n=10)
        retener.set()
        await adapter.stop()

        assert insp is not None
        assert insp.transcript_incomplete is True


# ---------------------------------------------------------------------------
# 5.4 — inspect describe el workdir (REQ-BGW-9) y expone los insumos (REQ-BGW-14)
# ---------------------------------------------------------------------------


class TestInspectConWorkdir:
    async def test_lista_los_ficheros_del_workdir(self, store, raiz: Path) -> None:
        """REQ-BGW-9 S1."""
        retener = asyncio.Event()
        adapter, _ = _armar(
            store=store,
            one_shot_for={"r": _hijo_que_anota(1, retener=retener)},
            workdirs=_gestor(raiz),
        )
        await adapter.start()
        encolada = await _encolar_completo(adapter)
        for nombre in ("informe.md", "datos.csv", "log.txt"):
            (Path(encolada.workdir) / nombre).write_text(nombre)  # type: ignore[arg-type]
        await asyncio.sleep(0.05)

        insp = await adapter.inspect(encolada.task_id, "inaki", last_n=10)
        retener.set()
        await adapter.stop()

        assert insp is not None and insp.workdir is not None
        assert insp.workdir.exists is True
        assert insp.workdir.path == encolada.workdir
        assert insp.workdir.relative_path == f"dwss/{Path(encolada.workdir).name}/"  # type: ignore[arg-type]
        assert [e.name for e in insp.workdir.entries] == ["datos.csv", "informe.md", "log.txt"]
        assert insp.workdir.entries_total == 3

    async def test_un_workdir_borrado_a_mano_dice_que_ya_no_existe(self, store, raiz: Path) -> None:
        """REQ-BGW-9 S2: nunca se finge contenido."""
        adapter, _ = _armar(
            store=store, one_shot_for={"r": _one_shot(_JSON_OK)}, workdirs=_gestor(raiz)
        )
        await adapter.start()
        encolada = await _encolar_completo(adapter)
        await asyncio.sleep(0.15)
        shutil.rmtree(encolada.workdir)  # type: ignore[arg-type]

        insp = await adapter.inspect(encolada.task_id, "inaki", last_n=10)
        await adapter.stop()

        assert insp is not None and insp.workdir is not None
        assert insp.workdir.exists is False
        assert insp.workdir.entries == []
        assert insp.workdir.note == "workdir no longer exists"

    async def test_sin_workdir_la_inspeccion_no_lo_trae(self, store) -> None:
        adapter, _ = _armar(store=store, one_shot_for={"r": _one_shot(_JSON_OK)})
        await adapter.start()
        task_id = await _encolar(adapter)
        await asyncio.sleep(0.15)

        insp = await adapter.inspect(task_id, "inaki", last_n=10)
        await adapter.stop()

        assert insp is not None
        assert insp.workdir is None
        assert insp.task.input_files == []

    async def test_inspect_no_crea_ni_borra_nada_del_workdir(self, store, raiz: Path) -> None:
        adapter, _ = _armar(
            store=store, one_shot_for={"r": _one_shot(_JSON_OK)}, workdirs=_gestor(raiz)
        )
        await adapter.start()
        encolada = await _encolar_completo(adapter)
        await asyncio.sleep(0.15)
        antes = sorted(p.name for p in (raiz.resolve() / "dwss").iterdir())
        shutil.rmtree(encolada.workdir)  # type: ignore[arg-type]
        sin_dir = sorted(p.name for p in (raiz.resolve() / "dwss").iterdir())

        await adapter.inspect(encolada.task_id, "inaki", last_n=10)
        despues = sorted(p.name for p in (raiz.resolve() / "dwss").iterdir())
        await adapter.stop()

        assert antes != sin_dir
        assert despues == sin_dir  # inspect sobre un dir ausente no lo recrea

    async def test_una_bg_m_viva_con_workdir_tambien_lo_describe(self, raiz: Path) -> None:
        retener = asyncio.Event()
        adapter, _ = _armar(
            store=None,
            one_shot_for={"r": _hijo_que_anota(1, retener=retener)},
            workdirs=_gestor(raiz),
        )
        await adapter.start()
        encolada = await _encolar_completo(adapter)
        (Path(encolada.workdir) / "parcial.md").write_text("x")  # type: ignore[arg-type]
        await asyncio.sleep(0.05)

        insp = await adapter.inspect(encolada.task_id, "inaki", last_n=10)
        retener.set()
        await adapter.stop()

        assert encolada.task_id == "bg-m1"
        assert insp is not None and insp.persisted is False
        assert insp.workdir is not None
        assert [e.name for e in insp.workdir.entries] == ["parcial.md"]

    async def test_los_insumos_copiados_viajan_en_la_inspeccion(self, store, raiz: Path) -> None:
        """REQ-BGW-14: los basenames sobreviven en la fila y ``inspect`` los devuelve."""
        _insumos(raiz)
        adapter, _ = _armar(
            store=store, one_shot_for={"r": _one_shot(_JSON_OK)}, workdirs=_gestor(raiz)
        )
        await adapter.start()
        encolada = await _encolar_completo(adapter, input_files=["notas/a.md", "datos.csv"])
        await asyncio.sleep(0.15)
        await adapter.stop()

        nuevo, _ = _armar(store=store, workdirs=_gestor(raiz))
        await nuevo.start()  # "reinicio": la inspección sale de la fila
        insp = await nuevo.inspect(encolada.task_id, "inaki", last_n=10)
        await nuevo.stop()

        assert insp is not None
        assert insp.task.input_files == ["a.md", "datos.csv"]
        assert insp.workdir is not None
        assert {e.name for e in insp.workdir.entries} == {"a.md", "datos.csv"}

    async def test_sin_gestor_una_fila_con_workdir_se_describe_sin_listar(self, store) -> None:
        """Config cambiada entre arranques: la fila trae workdir pero ya no hay gestor."""
        await store.open()
        t = _fila_nueva()
        t.workdir = "/ws/dwss/abcd1234"
        task_id = await store.create(t)
        await store.update(task_id, status="delivered", finished_at=datetime.now(timezone.utc))
        adapter, _ = _armar(store=store)
        await adapter.start()

        insp = await adapter.inspect(task_id, "inaki", last_n=10)
        await adapter.stop()

        assert insp is not None and insp.workdir is not None
        assert insp.workdir.path == "/ws/dwss/abcd1234"
        assert insp.workdir.entries == []
        assert insp.workdir.note is not None


class TestPodaSinGestorDeWorkdirs:
    async def test_una_fila_podada_con_workdir_sin_gestor_loguea_error_y_no_borra_nada(
        self, store, tmp_path: Path, caplog
    ) -> None:
        caplog.set_level(logging.ERROR, logger=_LOGGER)
        huerfano = tmp_path / "ws" / "dwss" / "abcd1234"
        huerfano.mkdir(parents=True)
        await store.open()
        t = _fila_nueva()
        t.workdir = str(huerfano)
        task_id = await store.create(t)
        await store.update(
            task_id, status="delivered", finished_at=datetime.now(timezone.utc) - timedelta(days=30)
        )
        adapter, _ = _armar(store=store)

        await adapter.start()
        fila = await store.get(task_id, "inaki")
        await adapter.stop()

        assert fila is None  # la fila SÍ se podó: la retención no depende del gestor
        assert huerfano.is_dir()
        assert any("sin borrar" in r.getMessage() for r in _errores(caplog))
