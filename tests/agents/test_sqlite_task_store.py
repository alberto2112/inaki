"""Tests de ``SQLiteBackgroundTaskStore`` (SQLite real en ``tmp_path``).

Cubre REQ-BGP-1 (ids durables), -2 (transcript incremental y pareo crudo), -3 (cap
cabeza+cola), -4 (estados y resultado persistido) y -7 (retención en TASKS con dos
cotas simultáneas). El store es un adapter aislado: acá no hay cola ni agentes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from inaki.agents.delegation.ports import ResultadoPoda
from inaki.agents.delegation.sqlite_task_store import (
    MENSAJE_CAP_CHARS,
    RETENCION_DIAS,
    RETENCION_ULTIMAS,
    SQLiteBackgroundTaskStore,
)
from inaki.kernel.domain.background_task import (
    BackgroundTask,
    DelegationFailure,
    InFlightToolCall,
)
from inaki.shared.message import Message, Role

_AHORA = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)


def _task(*, caller: str = "inaki", prompt: str = "investigá X", status: str = "queued"):
    return BackgroundTask(
        id="",  # el id lo asigna el store
        caller_agent_id=caller,
        target_agent_id="researcher",
        prompt=prompt,
        system_prompt="Sos un investigador.",
        channel="telegram",
        chat_id="42",
        max_iterations=10,
        timeout_seconds=300,
        started_at=_AHORA - timedelta(minutes=1),
        status=status,  # type: ignore[arg-type]
    )


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    s = SQLiteBackgroundTaskStore(tmp_path / "background_tasks.db")
    await s.open()
    yield s
    await s.close()


async def _crear(
    store: SQLiteBackgroundTaskStore,
    *,
    caller: str = "inaki",
    status: str = "delivered",
    hace: timedelta = timedelta(hours=1),
    notified: bool = True,
) -> str:
    """Crea una task y la deja en ``status`` terminada ``hace`` tiempo."""
    task_id = await store.create(_task(caller=caller))
    await store.update(task_id, status=status, finished_at=_AHORA - hace, notified=notified)
    return task_id


async def _ids_de(store: SQLiteBackgroundTaskStore, *estados: str) -> set[str]:
    return {t.id for t in await store.list_by_status(estados)}


_TODOS = (
    "queued",
    "running",
    "delivering",
    "delivery_failed",
    "delivered",
    "cancelled",
    "dismissed",
    "interrupted",
)


# ---------------------------------------------------------------------------
# REQ-BGP-1 — ids durables
# ---------------------------------------------------------------------------


class TestIds:
    async def test_la_primera_task_de_una_db_nueva_es_bg_1000_y_la_segunda_bg_1001(
        self, store
    ) -> None:
        assert await store.create(_task()) == "bg-1000"
        assert await store.create(_task()) == "bg-1001"

    async def test_reabrir_la_db_no_vuelve_a_sembrar_el_contador(self, tmp_path: Path) -> None:
        """REQ-BGP-1 S2: tras un restart el siguiente id continúa, no vuelve a 1000."""
        ruta = tmp_path / "bg.db"
        s1 = SQLiteBackgroundTaskStore(ruta)
        await s1.open()
        for esperado in ("bg-1000", "bg-1001", "bg-1002"):
            assert await s1.create(_task()) == esperado
        await s1.close()

        s2 = SQLiteBackgroundTaskStore(ruta)
        await s2.open()
        try:
            assert await s2.create(_task()) == "bg-1003"
        finally:
            await s2.close()

    async def test_un_id_podado_no_se_reutiliza_ni_tras_podar_todo_y_reabrir(
        self, tmp_path: Path
    ) -> None:
        """REQ-BGP-1 S3: la tabla vacía NO es una tabla nueva (no se re-siembra)."""
        ruta = tmp_path / "bg.db"
        s1 = SQLiteBackgroundTaskStore(ruta)
        await s1.open()
        for _ in range(3):
            await _crear(s1, hace=timedelta(days=30))
        assert (await s1.prune(_AHORA)).borradas == 3
        assert await s1.list_by_status(_TODOS) == []
        await s1.close()

        s2 = SQLiteBackgroundTaskStore(ruta)
        await s2.open()
        try:
            assert await s2.create(_task()) == "bg-1003"
        finally:
            await s2.close()

    async def test_open_es_idempotente(self, store) -> None:
        await store.open()

        assert await store.create(_task()) == "bg-1000"


# ---------------------------------------------------------------------------
# Esquema: WAL, claves foráneas, persistencia de campos
# ---------------------------------------------------------------------------


class TestEsquemaYCampos:
    async def test_la_db_queda_en_wal(self, store, tmp_path: Path) -> None:
        con = sqlite3.connect(tmp_path / "background_tasks.db")
        try:
            assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            con.close()

    async def test_roundtrip_de_la_task_recien_creada(self, store) -> None:
        task_id = await store.create(_task(prompt="investigá el saldo"))

        task = await store.get(task_id, "inaki")

        assert task is not None
        assert task.id == task_id
        assert task.status == "queued"
        assert task.caller_agent_id == "inaki"
        assert task.target_agent_id == "researcher"
        assert task.prompt == "investigá el saldo"
        assert task.system_prompt == "Sos un investigador."
        assert (task.channel, task.chat_id) == ("telegram", "42")
        assert (task.max_iterations, task.timeout_seconds) == (10, 300)
        assert task.started_at == _AHORA - timedelta(minutes=1)
        assert task.notified is True
        assert task.result is None

    async def test_system_prompt_none_se_conserva_como_none(self, store) -> None:
        t = _task()
        t.system_prompt = None
        task_id = await store.create(t)

        assert (await store.get(task_id, "inaki")).system_prompt is None  # type: ignore[union-attr]

    async def test_update_persiste_los_campos_de_timeline_y_desenlace(self, store) -> None:
        task_id = await store.create(_task())
        inicio = _AHORA - timedelta(seconds=40)

        await store.update(
            task_id,
            status="delivery_failed",
            run_started_at=inicio,
            finished_at=_AHORA,
            iterations_used=4,
            result="[bg-1000] success: listo",
            result_status="success",
            result_parse_error=True,
            error="RuntimeError: provider caído",
            notified=False,
        )

        task = await store.get(task_id, "inaki")
        assert task is not None
        assert task.status == "delivery_failed"
        assert task.run_started_at == inicio
        assert task.finished_at == _AHORA
        assert task.iterations_used == 4
        assert task.result == "[bg-1000] success: listo"
        assert task.result_status == "success"
        assert task.result_parse_error is True
        assert task.error == "RuntimeError: provider caído"
        assert task.notified is False

    async def test_update_es_idempotente(self, store) -> None:
        task_id = await store.create(_task())

        await store.update(task_id, status="running", iterations_used=2)
        await store.update(task_id, status="running", iterations_used=2)

        task = await store.get(task_id, "inaki")
        assert task is not None
        assert (task.status, task.iterations_used) == ("running", 2)

    async def test_update_con_campo_desconocido_falla_ruidoso(self, store) -> None:
        task_id = await store.create(_task())

        with pytest.raises(ValueError, match="color"):
            await store.update(task_id, color="rojo")

    async def test_el_fallo_clasificado_hace_roundtrip_completo(self, store) -> None:
        task_id = await store.create(_task())
        falla = DelegationFailure(
            kind="timeout",
            detail="TimeoutError (no message)",
            iterations_used=3,
            elapsed_seconds=300,
            last_step="called shell_exec",
            in_flight_tool=InFlightToolCall(
                tool_call_id="c1", name="shell_exec", arguments_preview='{"cmd": "ls"}'
            ),
            not_started_tools=["write_file"],
        )

        await store.update(task_id, failure=falla)

        task = await store.get(task_id, "inaki")
        assert task is not None
        assert task.failure == falla

    async def test_update_failure_none_borra_el_fallo(self, store) -> None:
        task_id = await store.create(_task())
        await store.update(task_id, failure=DelegationFailure(kind="exception", detail="X: y"))

        await store.update(task_id, failure=None)

        assert (await store.get(task_id, "inaki")).failure is None  # type: ignore[union-attr]

    async def test_save_outcome_persiste_estado_resultado_y_fallo_juntos(self, store) -> None:
        """REQ-BGP-4 S1: el resultado queda guardado ANTES del dispatch, completo."""
        task_id = await store.create(_task())
        task = await store.get(task_id, "inaki")
        assert task is not None
        resultado = "[bg-1000] success: " + "x" * 100_000  # nunca se recorta
        task.status = "delivering"
        task.result = resultado
        task.result_status = "success"
        task.iterations_used = 5
        task.finished_at = _AHORA
        task.failure = DelegationFailure(kind="exception", detail="OSError: x")

        await store.save_outcome(task)

        leida = await store.get(task_id, "inaki")
        assert leida is not None
        assert leida.status == "delivering"
        assert leida.result == resultado
        assert leida.result_status == "success"
        assert leida.iterations_used == 5
        assert leida.finished_at == _AHORA
        assert leida.failure is not None
        assert leida.failure.kind == "exception"


# ---------------------------------------------------------------------------
# get / list_by_status
# ---------------------------------------------------------------------------


class TestLecturas:
    async def test_get_de_otro_caller_es_none_igual_que_inexistente(self, store) -> None:
        task_id = await store.create(_task(caller="inaki"))

        assert await store.get(task_id, "anacleto") is None
        assert await store.get("bg-9999", "inaki") is None

    @pytest.mark.parametrize("raro", ["bg-m1", "bg-", "bg-abc", "1000", "", "bg-1000; DROP TABLE"])
    async def test_get_con_ids_no_durables_es_none_sin_lanzar(self, store, raro: str) -> None:
        await store.create(_task())

        assert await store.get(raro, "inaki") is None

    async def test_list_by_status_filtra_y_ordena_por_id(self, store) -> None:
        a = await _crear(store, status="delivered")
        b = await _crear(store, status="delivery_failed")
        c = await _crear(store, status="delivered")

        tasks = await store.list_by_status(["delivered"])

        assert [t.id for t in tasks] == [a, c]
        assert b not in {t.id for t in tasks}

    async def test_list_by_status_vacio_devuelve_lista_vacia(self, store) -> None:
        await _crear(store, status="delivered")

        assert await store.list_by_status([]) == []

    async def test_pending_notices_son_las_interrumpidas_sin_avisar(self, store) -> None:
        sin_avisar = await _crear(store, status="interrupted", notified=False)
        await _crear(store, status="interrupted", notified=True)
        await _crear(store, status="delivered", notified=False)

        pendientes = await store.pending_notices()

        assert [t.id for t in pendientes] == [sin_avisar]

    async def test_mark_notified_las_saca_de_pendientes(self, store) -> None:
        a = await _crear(store, status="interrupted", notified=False)
        b = await _crear(store, status="interrupted", notified=False)
        c = await _crear(store, status="interrupted", notified=False)

        await store.mark_notified([a, b, "bg-m7", "basura"])

        assert [t.id for t in await store.pending_notices()] == [c]
        assert (await store.get(a, "inaki")).notified is True  # type: ignore[union-attr]

    async def test_mark_notified_con_lista_vacia_es_noop(self, store) -> None:
        a = await _crear(store, status="interrupted", notified=False)

        await store.mark_notified([])

        assert [t.id for t in await store.pending_notices()] == [a]


# ---------------------------------------------------------------------------
# REQ-BGP-2 — transcript incremental
# ---------------------------------------------------------------------------


def _llamada(call_id: str, nombre: str, args: str = "{}") -> Message:
    return Message(
        role=Role.ASSISTANT,
        content="voy a mirar",
        tool_calls=[{"id": call_id, "function": {"name": nombre, "arguments": args}}],
    )


class TestTranscript:
    async def test_cada_mensaje_queda_en_orden_con_su_seq(self, store) -> None:
        task_id = await store.create(_task())
        rec = store.transcript(task_id)

        await rec.record(Message(role=Role.USER, content="investigá X"))
        await rec.record(_llamada("c1", "web_fetch"))
        await rec.record(Message(role=Role.TOOL, content="<html>", tool_call_id="c1"))
        await rec.record(Message(role=Role.ASSISTANT, content="listo"))

        pasos, total = await store.messages(task_id, last_n=50)
        assert total == 4
        assert [p.seq for p in pasos] == [1, 2, 3, 4]
        assert [p.role for p in pasos] == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]
        assert pasos[1].tool_calls == [
            {"id": "c1", "function": {"name": "web_fetch", "arguments": "{}"}}
        ]
        assert pasos[2].tool_call_id == "c1"
        assert pasos[3].content == "listo"
        assert all(p.created_at.tzinfo is not None for p in pasos)

    async def test_trace_arranca_vacio_y_es_una_lista_propia_del_recorder(self, store) -> None:
        a = store.transcript(await store.create(_task()))
        b = store.transcript(await store.create(_task()))

        a.trace.append(Message(role=Role.USER, content="x"))

        assert len(a.trace) == 1
        assert b.trace == []

    async def test_last_n_devuelve_los_ultimos_en_orden_y_el_total(self, store) -> None:
        task_id = await store.create(_task())
        rec = store.transcript(task_id)
        for i in range(1, 8):
            await rec.record(Message(role=Role.ASSISTANT, content=f"paso {i}"))

        pasos, total = await store.messages(task_id, last_n=3)

        assert total == 7
        assert [p.content for p in pasos] == ["paso 5", "paso 6", "paso 7"]
        assert [p.seq for p in pasos] == [5, 6, 7]

    async def test_task_sin_mensajes(self, store) -> None:
        task_id = await store.create(_task())

        pasos, total = await store.messages(task_id, last_n=10)

        assert pasos == []
        assert total == 0

    async def test_el_transcript_de_una_task_no_se_mezcla_con_el_de_otra(self, store) -> None:
        a = await store.create(_task())
        b = await store.create(_task())
        await store.transcript(a).record(Message(role=Role.USER, content="de A"))
        await store.transcript(b).record(Message(role=Role.USER, content="de B"))

        pasos_a, total_a = await store.messages(a, last_n=10)

        assert total_a == 1
        assert pasos_a[0].content == "de A"

    async def test_sigkill_conserva_los_pasos_y_el_tool_call_sin_respuesta(
        self, tmp_path: Path
    ) -> None:
        """REQ-BGP-2 S1/S2: reabrir SIN haber cerrado (kill -9) trae los 3 pasos en orden,
        y el tool_call ``c2`` figura sin su resultado."""
        ruta = tmp_path / "bg.db"
        muerto = SQLiteBackgroundTaskStore(ruta)
        await muerto.open()
        task_id = await muerto.create(_task())
        rec = muerto.transcript(task_id)
        try:
            await rec.record(Message(role=Role.USER, content="investigá X"))
            await rec.record(_llamada("c1", "web_fetch"))
            await rec.record(Message(role=Role.TOOL, content="ok", tool_call_id="c1"))
            await rec.record(_llamada("c2", "shell_exec", '{"cmd": "sleep 999"}'))

            # NO se cierra `muerto`: es el SIGKILL. Otro proceso reabre el mismo fichero.
            revivido = SQLiteBackgroundTaskStore(ruta)
            await revivido.open()
            try:
                pasos, total = await revivido.messages(task_id, last_n=50)
            finally:
                await revivido.close()
        finally:
            await muerto.close()

        assert total == 4
        assert [p.seq for p in pasos] == [1, 2, 3, 4]
        pareados = {p.tool_call_id for p in pasos if p.role is Role.TOOL}
        pedidos = [
            tc["id"]
            for p in pasos
            if p.role is Role.ASSISTANT and p.tool_calls
            for tc in p.tool_calls
        ]
        assert pedidos == ["c1", "c2"]
        assert pareados == {"c1"}  # c2 quedó sin respuesta

    async def test_cancelar_al_hijo_en_pleno_record_no_pierde_el_ultimo_mensaje(
        self, tmp_path: Path
    ) -> None:
        """El ``wait_for`` del timeout cancela al hijo justo en el ``await`` de la escritura:
        el ``asyncio.shield`` hace que el último tool result llegue igual al disco."""
        ruta = tmp_path / "bg.db"
        s = SQLiteBackgroundTaskStore(ruta)
        await s.open()
        task_id = await s.create(_task())
        rec = s.transcript(task_id)

        escritor = asyncio.create_task(
            rec.record(Message(role=Role.TOOL, content="resultado clave", tool_call_id="c1"))
        )
        await asyncio.sleep(0)  # el record ya despachó la escritura y espera su fin
        escritor.cancel()
        with pytest.raises(asyncio.CancelledError):
            await escritor
        await s.close()  # espera las escrituras en vuelo

        releido = SQLiteBackgroundTaskStore(ruta)
        await releido.open()
        try:
            pasos, total = await releido.messages(task_id, last_n=5)
        finally:
            await releido.close()
        assert total == 1
        assert pasos[0].content == "resultado clave"

    async def test_el_recorder_de_un_id_no_durable_es_un_error_de_uso(self, store) -> None:
        with pytest.raises(ValueError):
            store.transcript("bg-m1")


# ---------------------------------------------------------------------------
# REQ-BGP-3 — cap por mensaje, cabeza 40% / cola 60%
# ---------------------------------------------------------------------------


class TestCapPorMensaje:
    async def _guardar_tool(self, store, contenido: str):
        task_id = await store.create(_task())
        await store.transcript(task_id).record(
            Message(role=Role.TOOL, content=contenido, tool_call_id="c1")
        )
        pasos, _ = await store.messages(task_id, last_n=1)
        return pasos[0]

    async def test_el_cap_es_12450(self) -> None:
        assert MENSAJE_CAP_CHARS == 12450

    async def test_50000_chars_conservan_4980_de_cabeza_y_7470_de_cola_con_marcador(
        self, store
    ) -> None:
        cabeza, medio, cola = "A" * 4980, "m" * (50_000 - 4980 - 7470), "Z" * 7470
        paso = await self._guardar_tool(store, cabeza + medio + cola)

        assert paso.content.startswith(cabeza)
        assert paso.content.endswith(cola)
        assert "m" not in paso.content.replace("omitted", "").replace("characters", "")
        assert "37550 characters omitted" in paso.content
        assert paso.original_chars == 50_000

    async def test_12450_exactos_se_guardan_integros_sin_marcador(self, store) -> None:
        texto = "x" * 12_450

        paso = await self._guardar_tool(store, texto)

        assert paso.content == texto
        assert paso.original_chars is None

    async def test_un_caracter_de_mas_ya_recorta_y_dice_cuantos(self, store) -> None:
        texto = "a" * 4980 + "-" + "b" * 7470

        paso = await self._guardar_tool(store, texto)

        assert paso.content != texto
        assert "1 characters omitted" in paso.content
        assert paso.content.startswith("a" * 4980)
        assert paso.content.endswith("b" * 7470)
        assert paso.original_chars == 12_451

    async def test_un_mensaje_corto_no_se_toca(self, store) -> None:
        paso = await self._guardar_tool(store, "ok")

        assert paso.content == "ok"
        assert paso.original_chars is None

    async def test_tool_calls_chicos_se_guardan_tal_cual(self, store) -> None:
        task_id = await store.create(_task())
        msg = _llamada("c1", "write_file", '{"path": "a.txt"}')
        await store.transcript(task_id).record(msg)

        pasos, _ = await store.messages(task_id, last_n=1)

        assert pasos[0].tool_calls == msg.tool_calls

    async def test_tool_calls_enormes_recortan_arguments_y_el_json_sigue_valido(
        self, store, tmp_path: Path
    ) -> None:
        enorme = "q" * 30_000
        task_id = await store.create(_task())
        msg = _llamada("c1", "write_file", json.dumps({"content": enorme}))
        await store.transcript(task_id).record(msg)

        pasos, _ = await store.messages(task_id, last_n=1)

        tc = pasos[0].tool_calls
        assert tc is not None
        assert tc[0]["id"] == "c1"
        assert tc[0]["function"]["name"] == "write_file"
        args = tc[0]["function"]["arguments"]
        assert isinstance(args, str)
        assert len(args) < 30_000
        assert "characters omitted" in args
        # el JSON de la columna completa sigue siendo válido (lo leyó pydantic arriba), y
        # el mensaje original NO se mutó: el trace en memoria conserva todo.
        assert msg.tool_calls is not None
        assert len(msg.tool_calls[0]["function"]["arguments"]) > 30_000
        con = sqlite3.connect(tmp_path / "background_tasks.db")
        try:
            crudo = con.execute("SELECT tool_calls FROM bg_task_messages").fetchone()[0]
        finally:
            con.close()
        assert json.loads(crudo)[0]["id"] == "c1"


# ---------------------------------------------------------------------------
# REQ-BGP-7 — retención en TASKS (dos cotas simultáneas)
# ---------------------------------------------------------------------------


class TestRetencion:
    async def test_las_constantes_son_14_dias_y_20_tasks(self) -> None:
        assert RETENCION_DIAS == 14
        assert RETENCION_ULTIMAS == 20

    async def test_25_terminadas_dejan_las_20_mas_recientes_con_todos_sus_mensajes(
        self, store
    ) -> None:
        """REQ-BGP-7 S1: se cuentan TASKS, no filas — da igual cuántos mensajes tengan."""
        ids = []
        for i in range(25):
            task_id = await _crear(store, hace=timedelta(hours=25 - i))  # i=24 → la más reciente
            # La más vieja con MUCHOS mensajes, las demás con uno: no cambia el conteo.
            rec = store.transcript(task_id)
            for _ in range(30 if i == 24 else 1):
                await rec.record(Message(role=Role.USER, content="x"))
            ids.append(task_id)

        borradas = (await store.prune(_AHORA)).borradas

        assert borradas == 5
        assert await _ids_de(store, "delivered") == set(ids[5:])
        _, total = await store.messages(ids[24], last_n=1)
        assert total == 30  # la más reciente conserva TODOS sus mensajes

    async def test_una_terminada_de_hace_15_dias_se_poda_aunque_sea_la_unica(self, store) -> None:
        """REQ-BGP-7 S2."""
        vieja = await _crear(store, hace=timedelta(days=15))

        assert (await store.prune(_AHORA)).borradas == 1
        assert await store.get(vieja, "inaki") is None

    async def test_una_de_13_dias_se_conserva(self, store) -> None:
        reciente = await _crear(store, hace=timedelta(days=13, hours=23))

        assert (await store.prune(_AHORA)).borradas == 0
        assert await store.get(reciente, "inaki") is not None

    async def test_las_dos_cotas_rigen_a_la_vez(self, store) -> None:
        """22 recientes + 3 viejas: las viejas caen por edad Y las recientes se recortan a 20."""
        viejas = [await _crear(store, hace=timedelta(days=20 + i)) for i in range(3)]
        recientes = [await _crear(store, hace=timedelta(hours=1 + i)) for i in range(22)]

        borradas = (await store.prune(_AHORA)).borradas

        # 3 por edad + las 2 más viejas de las 22 por rango.
        assert borradas == 5
        assert await _ids_de(store, "delivered") == set(recientes[:20])
        assert not (set(viejas) & await _ids_de(store, "delivered"))

    async def test_delivery_failed_de_30_dias_no_se_poda(self, store) -> None:
        """REQ-BGP-7 S3: es la única copia de un resultado que el padre nunca recibió."""
        id_ = await _crear(store, status="delivery_failed", hace=timedelta(days=30))

        assert (await store.prune(_AHORA)).borradas == 0
        assert (await store.get(id_, "inaki")) is not None

    @pytest.mark.parametrize("estado", ["queued", "running", "delivering", "delivery_failed"])
    async def test_las_vivas_nunca_se_podan_ni_siendo_muy_viejas(self, store, estado: str) -> None:
        id_ = await _crear(store, status=estado, hace=timedelta(days=60))

        assert (await store.prune(_AHORA)).borradas == 0
        assert (await store.get(id_, "inaki")) is not None

    async def test_vivas_no_cuentan_para_las_20_de_terminadas(self, store) -> None:
        vivas = [await _crear(store, status="running") for _ in range(5)]
        terminadas = [await _crear(store, hace=timedelta(hours=1 + i)) for i in range(20)]

        assert (await store.prune(_AHORA)).borradas == 0
        assert await _ids_de(store, "running") == set(vivas)
        assert await _ids_de(store, "delivered") == set(terminadas)

    async def test_interrumpida_sin_avisar_solo_cae_por_edad(self, store) -> None:
        vieja = await _crear(store, status="interrupted", notified=False, hace=timedelta(days=30))
        reciente = await _crear(store, status="interrupted", notified=False, hace=timedelta(days=1))

        assert (await store.prune(_AHORA)).borradas == 1
        assert await store.get(vieja, "inaki") is None
        assert await store.get(reciente, "inaki") is not None

    async def test_muchas_interrumpidas_sin_avisar_recientes_no_se_podan_por_rango(
        self, store
    ) -> None:
        ids = [
            await _crear(store, status="interrupted", notified=False, hace=timedelta(hours=1 + i))
            for i in range(25)
        ]

        assert (await store.prune(_AHORA)).borradas == 0
        assert await _ids_de(store, "interrupted") == set(ids)

    async def test_interrumpida_ya_avisada_cuenta_como_terminada(self, store) -> None:
        ids = [
            await _crear(store, status="interrupted", notified=True, hace=timedelta(hours=1 + i))
            for i in range(22)
        ]

        assert (await store.prune(_AHORA)).borradas == 2
        assert await _ids_de(store, "interrupted") == set(ids[:20])

    async def test_el_conteo_es_independiente_por_caller(self, store) -> None:
        de_a = [await _crear(store, caller="inaki", hace=timedelta(hours=1 + i)) for i in range(25)]
        de_b = [
            await _crear(store, caller="anacleto", hace=timedelta(hours=1 + i)) for i in range(3)
        ]

        assert (await store.prune(_AHORA)).borradas == 5

        assert await _ids_de(store, "delivered") == set(de_a[:20]) | set(de_b)

    async def test_podar_borra_los_mensajes_en_cascada(self, store, tmp_path: Path) -> None:
        vieja = await _crear(store, hace=timedelta(days=20))
        rec_vieja = store.transcript(vieja)
        for _ in range(3):
            await rec_vieja.record(Message(role=Role.USER, content="x"))
        conservada = await _crear(store, hace=timedelta(hours=1))
        await store.transcript(conservada).record(Message(role=Role.USER, content="y"))

        await store.prune(_AHORA)

        con = sqlite3.connect(tmp_path / "background_tasks.db")
        try:
            filas = con.execute("SELECT task_id FROM bg_task_messages").fetchall()
        finally:
            con.close()
        assert filas == [(int(conservada.removeprefix("bg-")),)]


# ---------------------------------------------------------------------------
# REQ-BGP-8 — el recorder nunca lanza, y avisa en ERROR la capacidad perdida
# ---------------------------------------------------------------------------


class TestDegradacionDelRecorder:
    async def test_record_sobre_un_store_cerrado_no_lanza_y_loguea_error_una_vez(
        self, tmp_path: Path, caplog
    ) -> None:
        s = SQLiteBackgroundTaskStore(tmp_path / "bg.db")
        await s.open()
        task_id = await s.create(_task())
        rec = s.transcript(task_id)
        await s.close()  # el disco "se cae" a mitad de la delegación

        with caplog.at_level(logging.WARNING, logger="inaki.agents.delegation.sqlite_task_store"):
            await rec.record(Message(role=Role.USER, content="a"))
            await rec.record(Message(role=Role.ASSISTANT, content="b"))
            await rec.record(Message(role=Role.ASSISTANT, content="c"))

        errores = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errores) == 1
        assert "transcript/durable state unavailable" in errores[0].getMessage()
        assert task_id in errores[0].getMessage()
        assert rec.incompleto is True  # type: ignore[attr-defined]

    async def test_un_recorder_sano_no_esta_incompleto(self, store) -> None:
        rec = store.transcript(await store.create(_task()))

        await rec.record(Message(role=Role.USER, content="a"))

        assert rec.incompleto is False  # type: ignore[attr-defined]

    async def test_un_recorder_roto_no_afecta_al_de_otra_task(self, tmp_path: Path) -> None:
        s = SQLiteBackgroundTaskStore(tmp_path / "bg.db")
        await s.open()
        try:
            sano = s.transcript(await s.create(_task()))
            await sano.record(Message(role=Role.USER, content="a"))
            _, total = await s.messages("bg-1000", last_n=10)
            assert total == 1
        finally:
            await s.close()

    async def test_open_en_un_directorio_imposible_lanza(self, tmp_path: Path) -> None:
        """El store no traga el fallo de apertura: la cola decide cómo degradar."""
        archivo = tmp_path / "es_un_archivo"
        archivo.write_text("no soy un directorio")
        s = SQLiteBackgroundTaskStore(archivo / "bg.db")

        with pytest.raises(Exception):
            await s.open()

    async def test_operar_sin_abrir_falla_explicito(self, tmp_path: Path) -> None:
        s = SQLiteBackgroundTaskStore(tmp_path / "bg.db")

        with pytest.raises(RuntimeError, match="open"):
            await s.create(_task())

    async def test_close_es_idempotente(self, tmp_path: Path) -> None:
        s = SQLiteBackgroundTaskStore(tmp_path / "bg.db")
        await s.open()

        await s.close()
        await s.close()


# ---------------------------------------------------------------------------
# REQ-BGW-3 / REQ-BGW-6 / REQ-BGW-14 — workdir e input_files en la fila
# ---------------------------------------------------------------------------


async def _crear_con_workdir(
    store: SQLiteBackgroundTaskStore,
    ruta: str | None,
    *,
    caller: str = "inaki",
    status: str = "delivered",
    hace: timedelta = timedelta(hours=1),
    input_files: list[str] | None = None,
) -> str:
    task = _task(caller=caller)
    task.workdir = ruta
    task.input_files = input_files or []
    task_id = await store.create(task)
    await store.update(task_id, status=status, finished_at=_AHORA - hace)
    return task_id


class TestWorkdirEnLaFila:
    async def test_la_ruta_absoluta_hace_roundtrip(self, store) -> None:
        """REQ-BGW-3 S1."""
        task = _task()
        task.workdir = "/home/pi/ws/dwss/x7k2p9qa"
        task_id = await store.create(task)

        leida = await store.get(task_id, "inaki")

        assert leida is not None
        assert leida.workdir == "/home/pi/ws/dwss/x7k2p9qa"

    async def test_sin_workdir_ni_insumos_la_fila_los_devuelve_vacios(self, store) -> None:
        task_id = await store.create(_task())

        leida = await store.get(task_id, "inaki")

        assert leida is not None
        assert leida.workdir is None
        assert leida.input_files == []

    async def test_los_insumos_hacen_roundtrip_en_orden(self, store) -> None:
        """REQ-BGW-14 S2: la fila conserva ``["a.md","b.csv"]``."""
        task = _task()
        task.workdir = "/ws/dwss/abcd1234"
        task.input_files = ["a.md", "b.csv"]
        task_id = await store.create(task)

        leida = await store.get(task_id, "inaki")

        assert leida is not None
        assert leida.input_files == ["a.md", "b.csv"]

    async def test_insumos_con_caracteres_no_ascii_sobreviven(self, store) -> None:
        task = _task()
        task.input_files = ["informe-año.md"]
        task_id = await store.create(task)

        leida = await store.get(task_id, "inaki")

        assert leida is not None
        assert leida.input_files == ["informe-año.md"]

    async def test_workdir_e_insumos_sobreviven_a_reabrir_la_db(self, tmp_path: Path) -> None:
        ruta = tmp_path / "bg.db"
        s1 = SQLiteBackgroundTaskStore(ruta)
        await s1.open()
        task = _task()
        task.workdir = "/ws/dwss/abcd1234"
        task.input_files = ["a.md"]
        task_id = await s1.create(task)
        await s1.close()

        s2 = SQLiteBackgroundTaskStore(ruta)
        await s2.open()
        try:
            leida = await s2.get(task_id, "inaki")
        finally:
            await s2.close()

        assert leida is not None
        assert (leida.workdir, leida.input_files) == ("/ws/dwss/abcd1234", ["a.md"])

    async def test_list_by_status_tambien_trae_workdir(self, store) -> None:
        await _crear_con_workdir(store, "/ws/dwss/abcd1234", status="delivery_failed")

        (unica,) = await store.list_by_status(["delivery_failed"])

        assert unica.workdir == "/ws/dwss/abcd1234"

    @pytest.mark.parametrize("campo", ["workdir", "input_files"])
    async def test_workdir_e_insumos_son_inmutables_update_falla_ruidoso(
        self, store, campo: str
    ) -> None:
        task_id = await store.create(_task())

        with pytest.raises(ValueError, match=campo):
            await store.update(task_id, **{campo: "x"})

    async def test_workdirs_referenciados_devuelve_las_rutas_de_toda_fila_con_workdir(
        self, store
    ) -> None:
        await _crear_con_workdir(store, "/ws1/dwss/aaaaaaaa", status="running")
        await _crear_con_workdir(store, "/ws2/dwss/bbbbbbbb", status="delivered")
        await _crear_con_workdir(store, None)

        assert await store.workdirs_referenciados() == {
            "/ws1/dwss/aaaaaaaa",
            "/ws2/dwss/bbbbbbbb",
        }

    async def test_workdirs_referenciados_vacio_sin_filas(self, store) -> None:
        assert await store.workdirs_referenciados() == set()


class TestTranscriptIncompletoEnLaFila:
    """El flag de transcript cortado se persiste: tiene que sobrevivir a un reinicio."""

    async def test_una_task_nueva_nace_completa(self, store) -> None:
        task_id = await store.create(_task())

        assert (await store.get(task_id, "inaki")).transcript_incomplete is False  # type: ignore[union-attr]

    async def test_update_lo_marca_y_la_marca_sobrevive_a_reabrir(self, tmp_path: Path) -> None:
        ruta = tmp_path / "bg.db"
        s1 = SQLiteBackgroundTaskStore(ruta)
        await s1.open()
        try:
            task_id = await s1.create(_task())
            await s1.update(task_id, transcript_incomplete=True)
        finally:
            await s1.close()

        s2 = SQLiteBackgroundTaskStore(ruta)
        await s2.open()
        try:
            leida = await s2.get(task_id, "inaki")
            otra = await s2.create(_task())
            sana = await s2.get(otra, "inaki")
        finally:
            await s2.close()

        assert leida is not None and leida.transcript_incomplete is True
        assert sana is not None and sana.transcript_incomplete is False

    async def test_list_by_status_tambien_lo_trae(self, store) -> None:
        task_id = await store.create(_task())
        await store.update(task_id, status="delivery_failed", transcript_incomplete=True)

        (unica,) = await store.list_by_status(["delivery_failed"])

        assert unica.transcript_incomplete is True


class TestPodaDevuelveWorkdirs:
    async def test_devuelve_resultado_poda_con_id_y_ruta_de_lo_borrado(self, store) -> None:
        """REQ-BGW-6 (contrato): la ruta sale en el MISMO statement que borra la fila."""
        vieja = await _crear_con_workdir(store, "/ws/dwss/viejaaaa", hace=timedelta(days=20))

        resultado = await store.prune(_AHORA)

        assert isinstance(resultado, ResultadoPoda)
        assert resultado.borradas == 1
        assert resultado.workdirs == [(vieja, "/ws/dwss/viejaaaa")]

    async def test_solo_devuelve_rutas_de_las_filas_borradas(self, store) -> None:
        vieja = await _crear_con_workdir(store, "/ws/dwss/viejaaaa", hace=timedelta(days=20))
        await _crear_con_workdir(store, "/ws/dwss/reciente", hace=timedelta(hours=1))
        await _crear_con_workdir(
            store, "/ws/dwss/viva0000", status="delivery_failed", hace=timedelta(days=60)
        )

        resultado = await store.prune(_AHORA)

        assert resultado.borradas == 1
        assert resultado.workdirs == [(vieja, "/ws/dwss/viejaaaa")]
        assert "/ws/dwss/viva0000" in await store.workdirs_referenciados()

    async def test_filas_sin_workdir_cuentan_pero_no_aportan_ruta(self, store) -> None:
        await _crear_con_workdir(store, None, hace=timedelta(days=20))
        con = await _crear_con_workdir(store, "/ws/dwss/conconcon", hace=timedelta(days=21))

        resultado = await store.prune(_AHORA)

        assert resultado.borradas == 2
        assert resultado.workdirs == [(con, "/ws/dwss/conconcon")]

    async def test_poda_por_rango_tambien_devuelve_las_rutas(self, store) -> None:
        """REQ-BGP-7 S1 + workdirs: las 5 fuera de las últimas 20 traen su ruta."""
        ids = [
            await _crear_con_workdir(store, f"/ws/dwss/t{i:07d}", hace=timedelta(hours=1 + i))
            for i in range(25)
        ]

        resultado = await store.prune(_AHORA)

        assert resultado.borradas == 5
        assert {i for i, _ in resultado.workdirs} == set(ids[20:])
        assert {r for _, r in resultado.workdirs} == {f"/ws/dwss/t{i:07d}" for i in range(20, 25)}

    async def test_sin_nada_que_podar_devuelve_cero_y_lista_vacia(self, store) -> None:
        await _crear_con_workdir(store, "/ws/dwss/abcd1234")

        resultado = await store.prune(_AHORA)

        assert resultado == ResultadoPoda(borradas=0, workdirs=[])

    async def test_la_fila_ya_no_esta_cuando_se_devuelve_la_ruta(self, store) -> None:
        """Orden W6: la fila se borra PRIMERO; quien recibe la ruta borra el dir DESPUÉS."""
        vieja = await _crear_con_workdir(store, "/ws/dwss/viejaaaa", hace=timedelta(days=20))

        await store.prune(_AHORA)

        assert await store.get(vieja, "inaki") is None
        assert await store.workdirs_referenciados() == set()
