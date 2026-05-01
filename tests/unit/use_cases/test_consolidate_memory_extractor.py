"""Tests para `ConsolidateMemoryUseCase.set_extractor()` — extracción via sub-agente.

Cubre la rama nueva: cuando `memory.llm.agent_id` apunta a un sub-agente,
`AppContainer` Phase 6 inyecta el `RunAgentOneShotUseCase` del sub-agente
via `set_extractor()`, y `execute()` delega la extracción ahí en vez de
usar el prompt hardcodeado + LLM directo.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.domain.entities.message import Message, Role
from core.domain.errors import ConsolidationError
from core.domain.value_objects.llm_response import LLMResponse
from core.use_cases.consolidate_memory import ConsolidateMemoryUseCase
from infrastructure.config import MemoryConfig


@pytest.fixture
def memory_config(tmp_path: Path) -> MemoryConfig:
    return MemoryConfig(
        db_filename=":memory:",
        digest_size=3,
        digest_filename=str(tmp_path / "mem" / "digest.md"),
        min_relevance_score=0.5,
        keep_last_messages=20,
    )


@pytest.fixture
def use_case(mock_llm, mock_memory, mock_embedder, mock_history, memory_config):
    mock_memory.get_recent.return_value = []
    return ConsolidateMemoryUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        history=mock_history,
        agent_id="test",
        memory_config=memory_config,
    )


@pytest.fixture
def messages_in_history(mock_history):
    mock_history.load_uninfused.return_value = [
        Message(role=Role.USER, content="me gusta el café"),
        Message(role=Role.ASSISTANT, content="Anotado."),
    ]


# ---------------------------------------------------------------------------
# Default behavior preservado (sin set_extractor)
# ---------------------------------------------------------------------------


async def test_default_uses_internal_llm_when_no_extractor_set(
    use_case, mock_llm, mock_memory, mock_history, messages_in_history
):
    """Sin set_extractor → comportamiento legacy: prompt hardcodeado + llm.complete()."""
    mock_llm.complete.return_value = LLMResponse.of_text(
        '[{"content": "Le gusta el café", "relevance": 0.9, "tags": []}]'
    )

    await use_case.execute()

    mock_llm.complete.assert_awaited_once()
    # El system_prompt debe contener el extractor template hardcodeado
    call_kwargs = mock_llm.complete.await_args.kwargs
    assert "long-term memory extractor" in call_kwargs["system_prompt"]
    mock_memory.store.assert_called_once()


# ---------------------------------------------------------------------------
# Rama nueva: set_extractor → delegación a sub-agente
# ---------------------------------------------------------------------------


async def test_set_extractor_replaces_llm_path(
    use_case, mock_llm, mock_memory, mock_history, messages_in_history
):
    """Con extractor seteado, execute() llama al one-shot y NO al llm directo."""
    fake_one_shot = AsyncMock()
    fake_one_shot.execute.return_value = (
        '[{"content": "Le gusta el café", "relevance": 0.9, "tags": []}]'
    )

    use_case.set_extractor(fake_one_shot)
    await use_case.execute()

    fake_one_shot.execute.assert_awaited_once()
    mock_llm.complete.assert_not_awaited()
    mock_memory.store.assert_called_once()


async def test_set_extractor_passes_history_as_task(
    use_case, mock_llm, mock_memory, mock_history, messages_in_history
):
    """El historial formateado se pasa como `task` al one-shot, system_prompt=None."""
    fake_one_shot = AsyncMock()
    fake_one_shot.execute.return_value = "[]"

    use_case.set_extractor(fake_one_shot)
    await use_case.execute()

    call_kwargs = fake_one_shot.execute.await_args.kwargs
    # task debe contener los mensajes del historial formateados
    assert "me gusta el café" in call_kwargs["task"]
    assert "Anotado." in call_kwargs["task"]
    # system_prompt=None → el sub-agente usa su propio system_prompt
    assert call_kwargs["system_prompt"] is None


async def test_set_extractor_uses_configured_timeout_and_iterations(
    use_case, mock_history, messages_in_history
):
    """Los valores de set_extractor() llegan al execute del one-shot."""
    fake_one_shot = AsyncMock()
    fake_one_shot.execute.return_value = "[]"

    use_case.set_extractor(fake_one_shot, max_iterations=8, timeout_seconds=120)
    await use_case.execute()

    call_kwargs = fake_one_shot.execute.await_args.kwargs
    assert call_kwargs["max_iterations"] == 8
    assert call_kwargs["timeout_seconds"] == 120


async def test_set_extractor_default_limits_when_not_specified(
    use_case, mock_history, messages_in_history
):
    """Si set_extractor se llama sin keyword args, usa defaults razonables."""
    fake_one_shot = AsyncMock()
    fake_one_shot.execute.return_value = "[]"

    use_case.set_extractor(fake_one_shot)
    await use_case.execute()

    call_kwargs = fake_one_shot.execute.await_args.kwargs
    assert call_kwargs["max_iterations"] == 5
    assert call_kwargs["timeout_seconds"] == 180


async def test_set_extractor_response_with_markdown_wrapper_is_parsed(
    use_case, mock_memory, mock_history, messages_in_history
):
    """Si el sub-agente envuelve el JSON en ```json ... ```, _parse_facts lo maneja."""
    fake_one_shot = AsyncMock()
    fake_one_shot.execute.return_value = (
        '```json\n[{"content": "Le gusta el café", "relevance": 0.9, "tags": []}]\n```'
    )

    use_case.set_extractor(fake_one_shot)
    await use_case.execute()

    mock_memory.store.assert_called_once()


async def test_set_extractor_propagates_exception_as_consolidation_error(
    use_case, mock_history, messages_in_history
):
    """Si el one-shot lanza, se envuelve en ConsolidationError (igual que LLM directo)."""
    fake_one_shot = AsyncMock()
    fake_one_shot.execute.side_effect = RuntimeError("boom")

    use_case.set_extractor(fake_one_shot)

    with pytest.raises(ConsolidationError, match="extracción"):
        await use_case.execute()


async def test_set_extractor_empty_response_no_op(
    use_case, mock_memory, mock_history, messages_in_history
):
    """Sub-agente devuelve [] → consolidación termina ok, sin memories store."""
    fake_one_shot = AsyncMock()
    fake_one_shot.execute.return_value = "[]"

    use_case.set_extractor(fake_one_shot)
    result = await use_case.execute()

    mock_memory.store.assert_not_called()
    # Pero el flujo completa (mark_infused + trim)
    mock_history.mark_infused.assert_awaited_once_with("test")
    mock_history.trim.assert_awaited_once()
    assert "0 recuerdo" in result
