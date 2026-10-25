"""Tests de BackgroundTasksTool: list/cancel/inspect sobre la cola de delegaciones async.

La cola se mockea: acá se verifica el contrato de la tool (acciones, mensajes que
lee el LLM, ligado al caller), no la mecánica de cancelación (eso vive en
``test_background_queue.py``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from inaki.agents.delegation.background_tasks_tool import BackgroundTasksTool
from inaki.kernel.domain.background_task import (
    BackgroundTask,
    BackgroundTaskView,
    CancelOutcome,
    DelegationFailure,
    InFlightToolCall,
    TaskInspection,
    TranscriptEntry,
    WorkdirEntry,
    WorkdirState,
)
from inaki.shared.message import Role


def _tool(
    *,
    snapshot=None,
    cancel_outcome: CancelOutcome | None = None,
    inspection: TaskInspection | None = None,
):
    queue = MagicMock()
    queue.snapshot_inflight = MagicMock(return_value=snapshot or [])
    queue.cancel = AsyncMock(return_value=cancel_outcome)
    queue.inspect = AsyncMock(return_value=inspection)
    return BackgroundTasksTool(queue=queue, caller_agent_id="inaki"), queue


class TestList:
    async def test_lista_las_tasks_del_caller(self) -> None:
        vista = BackgroundTaskView(
            id="bg-2",
            target_agent_id="researcher",
            prompt_preview="investigá X",
            elapsed_seconds=21600,
            status="delivery_failed",
            error="RuntimeError: boom",
        )
        tool, queue = _tool(snapshot=[vista])

        res = await tool.execute(action="list")

        assert res.success
        queue.snapshot_inflight.assert_called_once_with("inaki")
        tasks = json.loads(res.output)["tasks"]
        assert tasks[0]["id"] == "bg-2"
        assert tasks[0]["status"] == "delivery_failed"
        assert tasks[0]["error"] == "RuntimeError: boom"

    async def test_lista_vacia_explica_por_que_no_hay_nada(self) -> None:
        tool, _ = _tool()

        res = await tool.execute(action="list")

        payload = json.loads(res.output)
        assert payload["tasks"] == []
        # REQ-BGD-8 S2: la cola es durable, la nota ya no dice que un restart pierde tasks.
        assert "restart" not in payload["note"]
        assert "lost" not in payload["note"]
        assert "inspect" in payload["note"]


class TestCancel:
    async def test_cancel_exige_task_id(self) -> None:
        tool, queue = _tool()

        res = await tool.execute(action="cancel")

        assert not res.success
        queue.cancel.assert_not_called()

    async def test_cancel_liga_la_task_al_caller(self) -> None:
        tool, queue = _tool(cancel_outcome=CancelOutcome(outcome="cancelled", task_id="bg-2"))

        res = await tool.execute(action="cancel", task_id="bg-2")

        assert res.success
        queue.cancel.assert_called_once_with("bg-2", "inaki")
        assert json.loads(res.output)["outcome"] == "cancelled"

    async def test_dismiss_devuelve_resultado_y_error(self) -> None:
        tool, _ = _tool(
            cancel_outcome=CancelOutcome(
                outcome="dismissed",
                task_id="bg-2",
                result="[bg-2] el informe completo",
                error="RuntimeError: boom",
            )
        )

        res = await tool.execute(action="cancel", task_id="bg-2")

        payload = json.loads(res.output)
        assert res.success
        assert payload["result"] == "[bg-2] el informe completo"
        assert payload["delivery_error"] == "RuntimeError: boom"

    async def test_not_found_no_afirma_que_nunca_existio(self) -> None:
        """Invariante search-history-retention-horizon: "no la tengo" no es "no existe" — el
        mensaje nombra las causas posibles (REQ-BGD-8 S3)."""
        tool, _ = _tool(cancel_outcome=CancelOutcome(outcome="not_found", task_id="bg-9"))

        res = await tool.execute(action="cancel", task_id="bg-9")

        assert not res.success
        assert "already delivered" in res.output
        assert "cancelled" in res.output
        assert "pruned" in res.output
        assert "does not exist" not in res.output
        assert "in-memory" not in res.output
        assert "restart" not in res.output

    async def test_cancel_de_una_terminada_dice_en_que_estado_quedo(self) -> None:
        tool, _ = _tool(
            cancel_outcome=CancelOutcome(outcome="finished", task_id="bg-5", status="delivered")
        )

        res = await tool.execute(action="cancel", task_id="bg-5")

        payload = json.loads(res.output)
        assert res.success
        assert payload["outcome"] == "finished"
        assert payload["status"] == "delivered"
        assert "nothing to cancel" in payload["message"]
        assert "inspect" in payload["message"]

    async def test_descartar_una_interrumpida_no_finge_que_habia_un_resultado(self) -> None:
        """Una ``interrupted`` no tiene resultado: el mensaje no dice que "había terminado"."""
        tool, _ = _tool(
            cancel_outcome=CancelOutcome(outcome="dismissed", task_id="bg-5", status="interrupted")
        )

        res = await tool.execute(action="cancel", task_id="bg-5")

        payload = json.loads(res.output)
        assert res.success
        assert payload["outcome"] == "dismissed"
        assert "interrupted" in payload["message"]
        assert "had finished" not in payload["message"]
        assert payload["result"] is None

    async def test_delivering_no_se_cancela(self) -> None:
        tool, _ = _tool(cancel_outcome=CancelOutcome(outcome="delivering", task_id="bg-2"))

        res = await tool.execute(action="cancel", task_id="bg-2")

        assert not res.success
        assert "being delivered" in res.output

    async def test_accion_desconocida(self) -> None:
        tool, _ = _tool()

        res = await tool.execute(action="purge")

        assert not res.success
        assert "list, cancel, inspect" in res.output


# ---------------------------------------------------------------------------
# inspect (REQ-BGI-1 / REQ-BGI-3)
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)


def _tarea(**campos) -> BackgroundTask:
    base: dict[str, Any] = dict(
        id="bg-1003",
        caller_agent_id="inaki",
        target_agent_id="researcher",
        prompt="investigá X",
        system_prompt=None,
        channel="telegram",
        chat_id="42",
        max_iterations=10,
        timeout_seconds=300,
        started_at=_T0,
        status="delivered",
    )
    base.update(campos)
    return BackgroundTask(**base)


def _paso(seq: int, role: Role = Role.ASSISTANT, content: str = "hola", **extra) -> TranscriptEntry:
    return TranscriptEntry(seq=seq, role=role, content=content, created_at=_T0, **extra)


def _inspeccion(task: BackgroundTask | None = None, **campos) -> TaskInspection:
    base: dict[str, Any] = dict(
        task=task or _tarea(), messages=[], messages_total=0, persisted=True
    )
    base.update(campos)
    return TaskInspection(**base)


class TestInspect:
    async def test_exige_task_id(self) -> None:
        tool, queue = _tool()

        res = await tool.execute(action="inspect")

        assert not res.success
        assert res.error == "task_id is required for action=inspect."
        queue.inspect.assert_not_called()

    async def test_liga_la_consulta_al_caller_y_usa_10_pasos_por_defecto(self) -> None:
        tool, queue = _tool(inspection=_inspeccion())

        res = await tool.execute(action="inspect", task_id="bg-1003")

        assert res.success
        queue.inspect.assert_awaited_once_with("bg-1003", "inaki", last_n=10)

    async def test_last_n_se_respeta_y_se_acota_a_50(self) -> None:
        tool, queue = _tool(inspection=_inspeccion())

        await tool.execute(action="inspect", task_id="bg-1003", last_n=3)
        await tool.execute(action="inspect", task_id="bg-1003", last_n=500)
        await tool.execute(action="inspect", task_id="bg-1003", last_n=0)

        assert [c.kwargs["last_n"] for c in queue.inspect.await_args_list] == [3, 50, 1]

    async def test_last_n_que_no_es_un_numero_es_un_error_claro(self) -> None:
        tool, queue = _tool(inspection=_inspeccion())

        res = await tool.execute(action="inspect", task_id="bg-1003", last_n="muchos")

        assert not res.success
        assert "last_n" in res.output
        queue.inspect.assert_not_called()

    async def test_task_no_encontrada_dice_que_ya_no_la_tiene_sin_afirmar_inexistencia(
        self,
    ) -> None:
        """REQ-BGI-3 S1 + invariante search-history-retention-horizon."""
        tool, _ = _tool(inspection=None)

        res = await tool.execute(action="inspect", task_id="bg-1001")

        assert not res.success
        assert "I no longer have bg-1001" in res.output
        assert "14 days" in res.output
        assert "last 20" in res.output
        assert "not one of your tasks" in res.output
        for prohibido in ("doesn't exist", "does not exist", "never existed"):
            assert prohibido not in res.output

    async def test_muestra_estado_tiempos_presupuesto_y_resultado(self) -> None:
        inicio = datetime(2026, 10, 25, 12, 0, 5, tzinfo=timezone.utc)
        fin = datetime(2026, 10, 25, 12, 1, 0, tzinfo=timezone.utc)
        tarea = _tarea(
            run_started_at=inicio,
            finished_at=fin,
            iterations_used=4,
            result_status="success",
            result_parse_error=False,
        )
        tool, _ = _tool(inspection=_inspeccion(tarea, messages=[_paso(1)], messages_total=1))

        res = await tool.execute(action="inspect", task_id="bg-1003")

        d = json.loads(res.output)
        assert d["task_id"] == "bg-1003"
        assert d["status"] == "delivered"
        assert d["target_agent_id"] == "researcher"
        assert d["prompt"] == "investigá X"
        assert d["created_at"] == _T0.isoformat()
        assert d["run_started_at"] == inicio.isoformat()
        assert d["finished_at"] == fin.isoformat()
        assert d["budget"] == {"timeout_seconds": 300, "max_iterations": 10}
        assert d["iterations_used"] == 4
        assert d["outcome"] == {"result_status": "success", "result_parse_error": False}
        assert d["failure"] is None
        assert d["persisted"] is True
        assert d["messages_total"] == 1
        assert d["messages_omitted"] == 0

    async def test_una_task_sin_desenlace_todavia_no_inventa_outcome(self) -> None:
        tool, _ = _tool(inspection=_inspeccion(_tarea(status="running")))

        d = json.loads((await tool.execute(action="inspect", task_id="bg-1003")).output)

        assert d["status"] == "running"
        assert d["outcome"] is None
        assert d["run_started_at"] is None

    async def test_el_fallo_expone_kind_detalle_ultimo_paso_y_la_tool_en_vuelo(self) -> None:
        """REQ-BGI-1 S1: timeout con la tool sin par visible."""
        falla = DelegationFailure(
            kind="timeout",
            detail="TimeoutError (no message)",
            iterations_used=3,
            elapsed_seconds=300,
            last_step="called shell_exec",
            in_flight_tool=InFlightToolCall(
                tool_call_id="c1", name="shell_exec", arguments_preview='{"cmd": "make"}'
            ),
            not_started_tools=["write_file"],
        )
        tool, _ = _tool(inspection=_inspeccion(_tarea(status="delivered", failure=falla)))

        d = json.loads((await tool.execute(action="inspect", task_id="bg-1003")).output)

        assert d["failure"]["kind"] == "timeout"
        assert d["failure"]["detail"] == "TimeoutError (no message)"
        assert d["failure"]["last_step"] == "called shell_exec"
        assert d["failure"]["in_flight_tool"] == {
            "name": "shell_exec",
            "tool_call_id": "c1",
            "arguments_preview": '{"cmd": "make"}',
        }
        assert d["failure"]["not_started_tools"] == ["write_file"]
        assert d["budget"]["timeout_seconds"] == 300

    async def test_el_error_de_entrega_viaja_si_lo_hay(self) -> None:
        tarea = _tarea(status="delivery_failed", error="RuntimeError: boom")
        tool, _ = _tool(inspection=_inspeccion(tarea))

        d = json.loads((await tool.execute(action="inspect", task_id="bg-1003")).output)

        assert d["delivery_error"] == "RuntimeError: boom"

    async def test_los_pasos_llevan_rol_contenido_y_pareo_de_tool_calls(self) -> None:
        pasos = [
            _paso(1, Role.USER, "investigá X"),
            _paso(
                2,
                Role.ASSISTANT,
                "",
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "web_fetch", "arguments": '{"url": "http://x"}'},
                    }
                ],
            ),
            _paso(3, Role.TOOL, "<html>", tool_call_id="c1"),
        ]
        tool, _ = _tool(inspection=_inspeccion(messages=pasos, messages_total=3))

        d = json.loads((await tool.execute(action="inspect", task_id="bg-1003")).output)

        assert [m["seq"] for m in d["messages"]] == [1, 2, 3]
        assert d["messages"][0] == {
            "seq": 1,
            "role": "user",
            "content": "investigá X",
            "tool_calls": None,
            "tool_call_id": None,
            "truncated_from": None,
        }
        assert d["messages"][1]["tool_calls"] == [
            {"id": "c1", "name": "web_fetch", "arguments": '{"url": "http://x"}'}
        ]
        assert d["messages"][2]["tool_call_id"] == "c1"

    async def test_un_tool_call_sin_respuesta_se_ve_sin_su_tool_message(self) -> None:
        """REQ-BGP-2 S2 visto desde el tool: el assistant con tool_call c1 y nada después."""
        pasos = [
            _paso(
                1,
                Role.ASSISTANT,
                "",
                tool_calls=[
                    {"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}}
                ],
            )
        ]
        tool, _ = _tool(inspection=_inspeccion(messages=pasos, messages_total=1))

        d = json.loads((await tool.execute(action="inspect", task_id="bg-1003")).output)

        assert d["messages"][0]["tool_calls"][0]["id"] == "c1"
        assert all(m["role"] != "tool" for m in d["messages"])

    async def test_cuenta_los_pasos_omitidos(self) -> None:
        tool, _ = _tool(inspection=_inspeccion(messages=[_paso(9), _paso(10)], messages_total=10))

        d = json.loads((await tool.execute(action="inspect", task_id="bg-1003")).output)

        assert d["messages_total"] == 10
        assert d["messages_omitted"] == 8

    async def test_el_contenido_largo_se_acota_a_1500_con_cabeza_y_cola(self) -> None:
        largo = "A" * 3000 + "FIN-DEL-TRACEBACK"
        tool, _ = _tool(
            inspection=_inspeccion(messages=[_paso(1, content=largo)], messages_total=1)
        )

        d = json.loads((await tool.execute(action="inspect", task_id="bg-1003")).output)

        contenido = d["messages"][0]["content"]
        assert contenido.startswith("A" * 100)
        assert contenido.endswith("FIN-DEL-TRACEBACK")
        assert "characters omitted" in contenido
        assert len(contenido) < 1700

    async def test_un_contenido_corto_se_deja_integro(self) -> None:
        tool, _ = _tool(
            inspection=_inspeccion(messages=[_paso(1, content="x" * 1500)], messages_total=1)
        )

        d = json.loads((await tool.execute(action="inspect", task_id="bg-1003")).output)

        assert d["messages"][0]["content"] == "x" * 1500

    async def test_el_recorte_del_store_se_informa_como_truncated_from(self) -> None:
        paso = _paso(1, content="recortado", original_chars=50000)
        tool, _ = _tool(inspection=_inspeccion(messages=[paso], messages_total=1))

        d = json.loads((await tool.execute(action="inspect", task_id="bg-1003")).output)

        assert d["messages"][0]["truncated_from"] == 50000

    async def test_los_argumentos_largos_de_un_tool_call_tambien_se_acotan(self) -> None:
        enormes = '{"content": "' + "Z" * 5000 + '"}'
        paso = _paso(
            1,
            tool_calls=[
                {"id": "c1", "type": "function", "function": {"name": "w", "arguments": enormes}}
            ],
        )
        tool, _ = _tool(inspection=_inspeccion(messages=[paso], messages_total=1))

        d = json.loads((await tool.execute(action="inspect", task_id="bg-1003")).output)

        assert len(d["messages"][0]["tool_calls"][0]["arguments"]) < 1700

    async def test_el_prompt_se_acota_a_500(self) -> None:
        tool, _ = _tool(inspection=_inspeccion(_tarea(prompt="p" * 2000)))

        d = json.loads((await tool.execute(action="inspect", task_id="bg-1003")).output)

        assert len(d["prompt"]) <= 501

    async def test_sin_transcript_durable_lo_declara_en_la_nota(self) -> None:
        """REQ-BGP-8: ``bg-m*`` o store caído → ``persisted=False``, y el tool lo dice."""
        tool, _ = _tool(
            inspection=_inspeccion(_tarea(id="bg-m3", status="running"), persisted=False)
        )

        d = json.loads((await tool.execute(action="inspect", task_id="bg-m3")).output)

        assert d["persisted"] is False
        assert d["messages"] == []
        assert "no durable transcript" in d["note"]

    async def test_un_transcript_incompleto_lo_declara_en_la_nota(self) -> None:
        tool, _ = _tool(inspection=_inspeccion(transcript_incomplete=True))

        d = json.loads((await tool.execute(action="inspect", task_id="bg-1003")).output)

        assert d["persisted"] is True
        assert "incomplete" in d["note"]

    async def test_un_transcript_completo_no_lleva_nota(self) -> None:
        tool, _ = _tool(inspection=_inspeccion())

        d = json.loads((await tool.execute(action="inspect", task_id="bg-1003")).output)

        assert "note" not in d


class TestEsquemaDelTool:
    def test_inspect_es_una_accion_del_enum_y_last_n_un_parametro(self) -> None:
        tool, _ = _tool()
        props = tool.parameters_schema["properties"]

        assert "inspect" in props["action"]["enum"]
        assert props["last_n"]["type"] == "integer"
        assert "max 50" in props["last_n"]["description"]

    def test_la_descripcion_menciona_inspect_y_no_dice_que_un_restart_pierde_tasks(self) -> None:
        tool, _ = _tool()
        textos = (tool.description, json.dumps(tool.parameters_schema))

        assert "inspect" in tool.description
        for t in textos:
            assert "restart" not in t
            assert "lost" not in t
            assert "in-memory" not in t


class TestInspectWorkdirEInsumos:
    """REQ-BGW-9 / REQ-BGW-14: ``workdir`` e ``input_files`` en el JSON de ``inspect``."""

    async def test_sin_workdir_es_null_y_los_insumos_una_lista_vacia(self) -> None:
        tool, _ = _tool(inspection=_inspeccion())

        d = json.loads((await tool.execute(action="inspect", task_id="bg-1003")).output)

        assert d["workdir"] is None
        assert d["input_files"] == []

    async def test_con_workdir_muestra_ruta_relativa_absoluta_y_listado(self) -> None:
        estado = WorkdirState(
            path="/ws/dwss/x7k2p9qa",
            relative_path="dwss/x7k2p9qa/",
            exists=True,
            entries=[
                WorkdirEntry(name="informe.md", kind="file", size_bytes=120),
                WorkdirEntry(name="grafs", kind="dir"),
            ],
            entries_total=2,
        )
        tool, _ = _tool(inspection=_inspeccion(workdir=estado))

        d = json.loads((await tool.execute(action="inspect", task_id="bg-1003")).output)

        assert d["workdir"] == {
            "path": "dwss/x7k2p9qa/",
            "absolute_path": "/ws/dwss/x7k2p9qa",
            "exists": True,
            "entries": [
                {"name": "informe.md", "type": "file", "size_bytes": 120},
                {"name": "grafs", "type": "dir", "size_bytes": None},
            ],
            "entries_total": 2,
            "entries_omitted": 0,
        }

    async def test_un_listado_acotado_informa_cuantas_entradas_quedaron_afuera(self) -> None:
        estado = WorkdirState(
            path="/ws/dwss/x7k2p9qa",
            relative_path="dwss/x7k2p9qa/",
            exists=True,
            entries=[WorkdirEntry(name=f"f{i}", kind="file", size_bytes=1) for i in range(20)],
            entries_total=35,
        )
        tool, _ = _tool(inspection=_inspeccion(workdir=estado))

        d = json.loads((await tool.execute(action="inspect", task_id="bg-1003")).output)

        assert d["workdir"]["entries_total"] == 35
        assert d["workdir"]["entries_omitted"] == 15
        assert len(d["workdir"]["entries"]) == 20

    async def test_un_workdir_borrado_dice_que_ya_no_existe_y_no_lista_nada(self) -> None:
        estado = WorkdirState(
            path="/ws/dwss/x7k2p9qa",
            relative_path="dwss/x7k2p9qa/",
            exists=False,
            entries=[],
            entries_total=0,
            note="workdir no longer exists",
        )
        tool, _ = _tool(inspection=_inspeccion(workdir=estado))

        d = json.loads((await tool.execute(action="inspect", task_id="bg-1003")).output)

        assert d["workdir"]["exists"] is False
        assert d["workdir"]["note"] == "workdir no longer exists"
        assert d["workdir"]["entries"] == []

    async def test_los_insumos_figuran_por_basename(self) -> None:
        tool, _ = _tool(inspection=_inspeccion(_tarea(input_files=["a.md", "b.csv"])))

        d = json.loads((await tool.execute(action="inspect", task_id="bg-1003")).output)

        assert d["input_files"] == ["a.md", "b.csv"]
