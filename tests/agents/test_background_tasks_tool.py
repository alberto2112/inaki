"""Tests de BackgroundTasksTool: list/cancel sobre la cola de delegaciones async.

La cola se mockea: acá se verifica el contrato de la tool (acciones, mensajes que
lee el LLM, ligado al caller), no la mecánica de cancelación (eso vive en
``test_background_queue.py``).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from inaki.agents.delegation.background_tasks_tool import BackgroundTasksTool
from inaki.kernel.domain.background_task import BackgroundTaskView, CancelOutcome


def _tool(*, snapshot=None, cancel_outcome: CancelOutcome | None = None):
    queue = MagicMock()
    queue.snapshot_inflight = MagicMock(return_value=snapshot or [])
    queue.cancel = MagicMock(return_value=cancel_outcome)
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
        assert "restart" in payload["note"]


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
        """Invariante search-history-retention-horizon: la cola es in-memory, así
        que "no la tengo" no es "no existe" — el mensaje nombra las causas."""
        tool, _ = _tool(cancel_outcome=CancelOutcome(outcome="not_found", task_id="bg-9"))

        res = await tool.execute(action="cancel", task_id="bg-9")

        assert not res.success
        assert "restart" in res.output
        assert "does not exist" not in res.output

    async def test_delivering_no_se_cancela(self) -> None:
        tool, _ = _tool(cancel_outcome=CancelOutcome(outcome="delivering", task_id="bg-2"))

        res = await tool.execute(action="cancel", task_id="bg-2")

        assert not res.success
        assert "being delivered" in res.output

    async def test_accion_desconocida(self) -> None:
        tool, _ = _tool()

        res = await tool.execute(action="purge")

        assert not res.success
        assert "list, cancel" in res.output
