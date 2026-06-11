"""Tests para la inyección de la sección de delegaciones in-flight en el
system prompt de `RunAgentUseCase` (REQ-BGD-7).

Cuando la queue de background-delegation está wired y devuelve tasks in-flight,
el system prompt assembled debe contener la sección `## In-flight background
delegations` con la lista de bullets. La sección está en INGLÉS
(convención del proyecto: system prompts en inglés).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.domain.entities.background_task import BackgroundTaskView
from core.domain.value_objects.llm_response import LLMResponse
from core.use_cases.run_agent import RunAgentUseCase, _render_in_flight_section
from infrastructure.container import build_run_agent_settings


def _view(
    *,
    id: str = "bg-1",
    target: str = "researcher",
    preview: str = "investigá X",
    elapsed: int = 5,
    status: str = "running",
) -> BackgroundTaskView:
    return BackgroundTaskView(
        id=id,
        target_agent_id=target,
        prompt_preview=preview,
        elapsed_seconds=elapsed,
        status=status,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Pure function — `_render_in_flight_section`
# ---------------------------------------------------------------------------


class TestRenderInFlightSection:
    def test_seccion_arranca_con_heading_en_ingles(self) -> None:
        out = _render_in_flight_section([_view()])

        assert out.startswith("## In-flight background delegations")

    def test_bullet_incluye_id_target_elapsed_y_preview(self) -> None:
        out = _render_in_flight_section(
            [
                _view(id="bg-7", target="researcher", preview="averiguá saldo", elapsed=32),
            ]
        )

        assert "bg-7 → researcher" in out
        assert "started 32s ago" in out
        assert '"averiguá saldo"' in out

    def test_multiple_tasks_se_listan_como_bullets_separados(self) -> None:
        """Triangulación: el formato escala a N tasks."""
        out = _render_in_flight_section(
            [
                _view(id="bg-1", target="researcher", preview="a", elapsed=10),
                _view(id="bg-2", target="coder", preview="b", elapsed=3),
            ]
        )

        assert "- bg-1 → researcher" in out
        assert "- bg-2 → coder" in out

    def test_instruccion_explica_el_marker(self) -> None:
        """El LLM debe entender qué significa recibir un mensaje `[bg-N] ...`."""
        out = _render_in_flight_section([_view()])

        assert "`[bg-N] ...`" in out
        assert "NOT user input" in out


# ---------------------------------------------------------------------------
# Integración — RunAgentUseCase inyecta la sección en el system prompt
# ---------------------------------------------------------------------------


@pytest.fixture
def _build_use_case(
    agent_config, mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools
):
    """Builder que acepta una queue mock (o None) y devuelve un use case listo."""

    def _build(queue=None):
        return RunAgentUseCase(
            llm=mock_llm,
            memory=mock_memory,
            embedder=mock_embedder,
            skills=mock_skills,
            history=mock_history,
            tools=mock_tools,
            settings=build_run_agent_settings(agent_config),
            background_queue=queue,
        )

    return _build


class TestSystemPromptInjection:
    async def test_snapshot_con_tasks_inyecta_seccion_en_system_prompt(
        self, _build_use_case, mock_llm
    ) -> None:
        """REQ-BGD-7: con tasks in-flight, el system prompt contiene la sección."""
        queue = MagicMock()
        queue.snapshot_inflight = MagicMock(
            return_value=[
                _view(id="bg-7", target="researcher", preview="averiguá saldo", elapsed=32),
            ]
        )
        use_case = _build_use_case(queue=queue)
        mock_llm.complete.return_value = LLMResponse.of_text("ok")

        await use_case.execute("hola")

        system_prompt = mock_llm.complete.call_args.args[1]
        assert "## In-flight background delegations" in system_prompt
        assert "bg-7 → researcher" in system_prompt

    async def test_snapshot_vacio_no_inyecta_seccion(self, _build_use_case, mock_llm) -> None:
        """REQ-BGD-7: snapshot vacío → no se agrega la sección."""
        queue = MagicMock()
        queue.snapshot_inflight = MagicMock(return_value=[])
        use_case = _build_use_case(queue=queue)
        mock_llm.complete.return_value = LLMResponse.of_text("ok")

        await use_case.execute("hola")

        system_prompt = mock_llm.complete.call_args.args[1]
        assert "## In-flight background delegations" not in system_prompt

    async def test_sin_queue_wired_no_inyecta_seccion(self, _build_use_case, mock_llm) -> None:
        """Triangulación: sin queue (default None) el feature está desactivado."""
        use_case = _build_use_case(queue=None)
        mock_llm.complete.return_value = LLMResponse.of_text("ok")

        await use_case.execute("hola")

        system_prompt = mock_llm.complete.call_args.args[1]
        assert "## In-flight background delegations" not in system_prompt

    async def test_snapshot_se_consulta_por_el_agent_id_correcto(
        self, _build_use_case, mock_llm, agent_config
    ) -> None:
        """REQ-BGD-7: snapshot_inflight se llama con el agent_id del use case."""
        queue = MagicMock()
        queue.snapshot_inflight = MagicMock(return_value=[])
        use_case = _build_use_case(queue=queue)
        mock_llm.complete.return_value = LLMResponse.of_text("ok")

        await use_case.execute("hola")

        queue.snapshot_inflight.assert_called_once_with(agent_config.id)
