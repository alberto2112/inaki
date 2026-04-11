"""Tests unitarios para ConsolidateMemoryUseCase — transaccionalidad crítica."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

import pytest

from core.domain.entities.memory import MemoryEntry
from core.domain.entities.message import Message, Role
from core.domain.errors import ConsolidationError
from core.use_cases.consolidate_memory import ConsolidateMemoryUseCase
from infrastructure.config import MemoryConfig


@pytest.fixture
def memory_config(tmp_path: Path) -> MemoryConfig:
    return MemoryConfig(
        db_path=":memory:",
        digest_size=3,
        digest_path=str(tmp_path / "mem" / "digest.md"),
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
        Message(role=Role.USER, content="me gusta Python"),
        Message(role=Role.ASSISTANT, content="Anotado."),
    ]


async def test_consolidation_trims_on_success(use_case, mock_llm, mock_memory, mock_history, messages_in_history):
    mock_llm.complete.return_value = '[{"content": "Le gusta Python", "relevance": 0.9, "tags": ["tech"]}]'

    result = await use_case.execute()

    mock_memory.store.assert_called_once()
    mock_history.trim.assert_called_once_with("test", keep_last=20)
    mock_history.clear.assert_not_called()
    assert "1 recuerdo" in result


async def test_consolidation_attributes_agent_id_to_stored_memory(
    use_case, mock_llm, mock_memory, mock_history, messages_in_history
):
    """Cada MemoryEntry persistido debe llevar el agent_id del agente que lo extrajo."""
    mock_llm.complete.return_value = (
        '[{"content": "fact A", "relevance": 0.9, "tags": []},'
        ' {"content": "fact B", "relevance": 0.8, "tags": []}]'
    )

    await use_case.execute()

    assert mock_memory.store.call_count == 2
    for call in mock_memory.store.call_args_list:
        entry = call.args[0]
        assert entry.agent_id == "test", (
            f"Se esperaba agent_id='test', se obtuvo {entry.agent_id!r}"
        )


async def test_consolidation_marks_messages_as_infused_after_persist(
    use_case, mock_llm, mock_memory, mock_history, messages_in_history
):
    """Tras persistir los recuerdos, se marcan los mensajes como infused."""
    mock_llm.complete.return_value = '[{"content": "Le gusta Python", "relevance": 0.9, "tags": []}]'

    await use_case.execute()

    mock_history.mark_infused.assert_called_once_with("test")


async def test_consolidation_mark_infused_called_before_trim(
    use_case, mock_llm, mock_memory, mock_history, messages_in_history
):
    """mark_infused debe ocurrir ANTES del trim para que el gate cierre a tiempo."""
    mock_llm.complete.return_value = "[]"
    call_order: list[str] = []
    mock_history.mark_infused.side_effect = lambda *a, **kw: call_order.append("mark_infused") or 0
    mock_history.trim.side_effect = lambda *a, **kw: call_order.append("trim")

    await use_case.execute()

    assert call_order == ["mark_infused", "trim"]


async def test_consolidation_mark_infused_failure_aborts_and_skips_trim(
    use_case, mock_llm, mock_memory, mock_history, messages_in_history
):
    """Si mark_infused falla, propagamos y NO truncamos."""
    mock_llm.complete.return_value = '[{"content": "fact", "relevance": 0.9, "tags": []}]'
    mock_history.mark_infused.side_effect = Exception("UPDATE failed")

    with pytest.raises(ConsolidationError):
        await use_case.execute()

    mock_history.trim.assert_not_called()


async def test_consolidation_is_idempotent_when_no_uninfused_messages(
    use_case, mock_history
):
    """Ejecutar /consolidate dos veces seguidas → la segunda es no-op total."""
    mock_history.load_uninfused.return_value = []

    result = await use_case.execute()

    assert "No hay mensajes nuevos" in result
    mock_history.mark_infused.assert_not_called()
    mock_history.trim.assert_not_called()


async def test_consolidation_does_not_trim_on_llm_failure(use_case, mock_llm, mock_history, messages_in_history):
    mock_llm.complete.side_effect = Exception("LLM timeout")

    with pytest.raises(ConsolidationError):
        await use_case.execute()

    mock_history.trim.assert_not_called()
    mock_history.clear.assert_not_called()


async def test_consolidation_does_not_trim_on_store_failure(use_case, mock_llm, mock_memory, mock_history, messages_in_history):
    mock_llm.complete.return_value = '[{"content": "Le gusta Python", "relevance": 0.9, "tags": []}]'
    mock_memory.store.side_effect = Exception("DB error")

    with pytest.raises(ConsolidationError):
        await use_case.execute()

    mock_history.trim.assert_not_called()


async def test_consolidation_returns_message_when_no_pending_messages(use_case, mock_history):
    """Sin mensajes uninfused → no-op idempotente."""
    mock_history.load_uninfused.return_value = []
    result = await use_case.execute()
    assert "No hay mensajes nuevos" in result
    mock_history.trim.assert_not_called()
    mock_history.mark_infused.assert_not_called()


async def test_consolidation_handles_empty_facts_list(use_case, mock_llm, mock_history, messages_in_history):
    """LLM dice no hay recuerdos relevantes → truncamos igual."""
    mock_llm.complete.return_value = "[]"
    result = await use_case.execute()
    mock_history.trim.assert_called_once_with("test", keep_last=20)


async def test_consolidation_strips_markdown_json(use_case, mock_llm, mock_memory, mock_history, messages_in_history):
    """El LLM a veces envuelve el JSON en ```json ... ```"""
    mock_llm.complete.return_value = '```json\n[{"content": "test", "relevance": 0.8, "tags": []}]\n```'
    await use_case.execute()
    mock_memory.store.assert_called_once()


async def test_consolidation_raises_on_invalid_json(use_case, mock_llm, mock_history, messages_in_history):
    mock_llm.complete.return_value = "esto no es json"
    with pytest.raises(ConsolidationError):
        await use_case.execute()
    mock_history.trim.assert_not_called()


# SC-15
async def test_consolidation_formats_message_with_timestamp(use_case, mock_llm, mock_memory, mock_history):
    ts = datetime(2026, 4, 9, 15, 30, 0, tzinfo=timezone.utc)
    mock_history.load_uninfused.return_value = [
        Message(role=Role.USER, content="prefiero café sin azúcar", timestamp=ts),
    ]
    mock_llm.complete.return_value = "[]"

    await use_case.execute()

    call_args = mock_llm.complete.call_args
    system_prompt = call_args.kwargs.get("system_prompt") or call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs["system_prompt"]
    assert "user [2026-04-09T15:30:00Z]: prefiero café sin azúcar" in system_prompt


# SC-16
async def test_consolidation_formats_message_without_timestamp(use_case, mock_llm, mock_memory, mock_history):
    mock_history.load_uninfused.return_value = [
        Message(role=Role.USER, content="prefiero café sin azúcar", timestamp=None),
    ]
    mock_llm.complete.return_value = "[]"

    await use_case.execute()

    call_args = mock_llm.complete.call_args
    system_prompt = call_args.kwargs.get("system_prompt") or call_args.kwargs["system_prompt"]
    assert "user: prefiero café sin azúcar" in system_prompt
    assert "[" not in system_prompt.split("user:")[1].split("\n")[0]


# SC-17
async def test_consolidation_sets_created_at_from_llm_timestamp(use_case, mock_llm, mock_memory, mock_history, messages_in_history):
    mock_llm.complete.return_value = '[{"content": "test", "relevance": 0.9, "tags": [], "timestamp": "2026-04-09T15:30:00Z"}]'

    await use_case.execute()

    entry = mock_memory.store.call_args.args[0]
    assert entry.created_at == datetime(2026, 4, 9, 15, 30, 0, tzinfo=timezone.utc)


async def test_consolidation_filters_below_min_relevance_score(
    use_case, mock_llm, mock_memory, mock_history, messages_in_history
):
    """Hechos con relevance < min_relevance_score no se embedean ni persisten."""
    mock_llm.complete.return_value = (
        '[{"content": "alto", "relevance": 0.9, "tags": []},'
        ' {"content": "medio-alto", "relevance": 0.51, "tags": []},'
        ' {"content": "bajo", "relevance": 0.3, "tags": []},'
        ' {"content": "muy bajo", "relevance": 0.1, "tags": []}]'
    )

    await use_case.execute()

    # Solo los dos primeros pasan el umbral 0.5
    assert mock_memory.store.call_count == 2
    stored_contents = {c.args[0].content for c in mock_memory.store.call_args_list}
    assert stored_contents == {"alto", "medio-alto"}
    # Trim igual (el LLM corrió con éxito)
    mock_history.trim.assert_called_once_with("test", keep_last=20)


async def test_consolidation_filters_all_when_all_below_threshold(
    use_case, mock_llm, mock_memory, mock_history, messages_in_history
):
    """Si TODOS los hechos están por debajo del umbral, no persistimos pero truncamos."""
    mock_llm.complete.return_value = '[{"content": "bajo", "relevance": 0.1, "tags": []}]'

    await use_case.execute()

    mock_memory.store.assert_not_called()
    mock_history.trim.assert_called_once_with("test", keep_last=20)


async def test_consolidation_uses_sentinel_fallback_when_keep_last_is_zero(
    tmp_path: Path, mock_llm, mock_memory, mock_embedder, mock_history
):
    """keep_last_messages=0 es sentinel → resuelve al fallback del sistema (84)."""
    cfg = MemoryConfig(
        db_path=":memory:",
        digest_size=3,
        digest_path=str(tmp_path / "mem" / "digest.md"),
        min_relevance_score=0.5,
        keep_last_messages=0,  # sentinel
    )
    mock_memory.get_recent.return_value = []
    mock_history.load_uninfused.return_value = [
        Message(role=Role.USER, content="hola"),
    ]
    mock_llm.complete.return_value = "[]"

    uc = ConsolidateMemoryUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        history=mock_history,
        agent_id="test",
        memory_config=cfg,
    )

    await uc.execute()

    mock_history.trim.assert_called_once_with("test", keep_last=84)


# SC-18
async def test_consolidation_falls_back_to_now_when_no_timestamp(use_case, mock_llm, mock_memory, mock_history, messages_in_history):
    mock_llm.complete.return_value = '[{"content": "test", "relevance": 0.9, "tags": []}]'
    before = datetime.now(timezone.utc)

    await use_case.execute()

    after = datetime.now(timezone.utc)
    entry = mock_memory.store.call_args.args[0]
    assert before <= entry.created_at <= after


# ---------------------------------------------------------------------------
# Phase 3 — digest write tests
# ---------------------------------------------------------------------------

def _make_entry(content: str, tags: list[str], created_at: datetime) -> MemoryEntry:
    return MemoryEntry(
        content=content,
        embedding=[0.1] * 384,
        relevance=0.9,
        tags=tags,
        created_at=created_at,
    )


# SC-03, SC-12, SC-13, AC-04 (a)
async def test_digest_file_written_with_correct_format(
    mock_llm, mock_memory, mock_embedder, mock_history, memory_config
):
    mock_history.load_uninfused.return_value = [
        Message(role=Role.USER, content="me gusta Python"),
        Message(role=Role.ASSISTANT, content="Anotado."),
    ]
    mock_llm.complete.return_value = '[{"content": "Le gusta Python", "relevance": 0.9, "tags": ["tech", "python"]}]'

    entry_with_tags = _make_entry(
        "Le gusta Python", ["tech", "python"], datetime(2026, 4, 9, tzinfo=timezone.utc)
    )
    entry_no_tags = _make_entry(
        "Usa LazyVim", [], datetime(2026, 4, 8, tzinfo=timezone.utc)
    )
    mock_memory.get_recent.return_value = [entry_with_tags, entry_no_tags]

    uc = ConsolidateMemoryUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        history=mock_history,
        agent_id="test",
        memory_config=memory_config,
    )
    await uc.execute()

    digest_file = memory_config.digest_path
    assert digest_file.exists()
    content = digest_file.read_text(encoding="utf-8")
    assert content.startswith("# Recuerdos sobre el usuario")
    assert "<!-- Generado por /consolidate —" in content
    assert "- [2026-04-09] Le gusta Python (tech, python)" in content
    assert "- [2026-04-08] Usa LazyVim" in content
    # No parenthetical for entry without tags
    assert "- [2026-04-08] Usa LazyVim\n" in content or content.endswith("- [2026-04-08] Usa LazyVim\n")


# SC-10, SC-11, AC-05 (b)
async def test_get_recent_called_with_configured_digest_size(
    use_case, mock_llm, mock_memory, mock_history, messages_in_history, memory_config
):
    mock_llm.complete.return_value = "[]"

    await use_case.execute()

    mock_memory.get_recent.assert_called_once_with(memory_config.digest_size)


# SC-09, FR-05, AC-04 (c)
async def test_trim_called_after_digest(
    use_case, mock_llm, mock_memory, mock_history, messages_in_history
):
    mock_llm.complete.return_value = "[]"
    call_order = []
    mock_memory.get_recent.side_effect = lambda *a, **kw: call_order.append("get_recent") or []
    mock_history.trim.side_effect = lambda *a, **kw: call_order.append("trim")

    await use_case.execute()

    assert "get_recent" in call_order
    assert "trim" in call_order
    assert call_order.index("trim") > call_order.index("get_recent")


# FR-09, NFR-03 (d)
async def test_write_digest_ioerror_does_not_abort_consolidation(
    use_case, mock_llm, mock_memory, mock_history, messages_in_history
):
    mock_llm.complete.return_value = "[]"

    with patch.object(Path, "write_text", side_effect=OSError("disk full")):
        result = await use_case.execute()

    mock_history.trim.assert_called_once_with("test", keep_last=20)
    assert result is not None


# SC-19, NFR-02 (e)
async def test_parent_directory_created_for_digest(
    mock_llm, mock_memory, mock_embedder, mock_history, tmp_path
):
    nested_path = tmp_path / "a" / "b" / "c" / "digest.md"
    assert not nested_path.parent.exists()

    cfg = MemoryConfig(digest_size=2, digest_path=str(nested_path))
    mock_memory.get_recent.return_value = []
    mock_history.load_uninfused.return_value = [
        Message(role=Role.USER, content="hola"),
    ]
    mock_llm.complete.return_value = "[]"

    uc = ConsolidateMemoryUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        history=mock_history,
        agent_id="test",
        memory_config=cfg,
    )
    await uc.execute()

    assert nested_path.parent.exists()
    assert nested_path.exists()
