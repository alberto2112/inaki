"""Tests para search_memory / delete_memory / update_memory tools."""

from __future__ import annotations

from datetime import datetime, timezone

from adapters.outbound.tools.memory_tools import (
    DeleteMemoryTool,
    SearchMemoryTool,
    UpdateMemoryTool,
)
from core.domain.entities.memory import MemoryEntry


def _entry(content: str, *, memory_id: str = "abc-1234", deleted: bool = False) -> MemoryEntry:
    return MemoryEntry(
        id=memory_id,
        content=content,
        embedding=[0.1] * 384,
        relevance=0.9,
        tags=["python"],
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        agent_id="test",
        channel="telegram",
        chat_id="-1001",
        deleted=deleted,
    )


# ---------------------------------------------------------------------------
# SearchMemoryTool
# ---------------------------------------------------------------------------


async def test_search_memory_returns_id_in_output(mock_memory, mock_embedder):
    entry = _entry("le gusta Python")
    mock_memory.search_with_scores.return_value = [(entry, 0.85)]

    tool = SearchMemoryTool(memory=mock_memory, embedder=mock_embedder)
    result = await tool.execute(query="python")

    assert result.success is True
    # El id DEBE estar en el output — el LLM lo necesita para delete/update
    assert "abc-1234" in result.output
    assert "le gusta Python" in result.output
    assert "score=0.850" in result.output


async def test_search_memory_empty_query_fails(mock_memory, mock_embedder):
    tool = SearchMemoryTool(memory=mock_memory, embedder=mock_embedder)
    result = await tool.execute(query="   ")

    assert result.success is False
    assert "query" in result.output.lower()


async def test_search_memory_no_results(mock_memory, mock_embedder):
    mock_memory.search_with_scores.return_value = []
    tool = SearchMemoryTool(memory=mock_memory, embedder=mock_embedder)

    result = await tool.execute(query="algo")

    assert result.success is True
    assert "No memories matched" in result.output


async def test_search_memory_caps_top_k_at_max(mock_memory, mock_embedder):
    mock_memory.search_with_scores.return_value = []
    tool = SearchMemoryTool(memory=mock_memory, embedder=mock_embedder)

    await tool.execute(query="x", top_k=999)

    # Debe haber pasado al repo top_k=20 (cap)
    call_kwargs = mock_memory.search_with_scores.call_args.kwargs
    assert call_kwargs["top_k"] == 20


# ---------------------------------------------------------------------------
# DeleteMemoryTool
# ---------------------------------------------------------------------------


async def test_delete_memory_happy_path(mock_memory):
    entry = _entry("borradito", deleted=True)
    mock_memory.delete.return_value = entry

    tool = DeleteMemoryTool(memory=mock_memory)
    result = await tool.execute(memory_id="abc-1234")

    assert result.success is True
    mock_memory.delete.assert_awaited_once_with("abc-1234")
    assert "abc-1234" in result.output
    assert "borradito" in result.output


async def test_delete_memory_not_found_is_success_no_op(mock_memory):
    mock_memory.delete.return_value = None
    tool = DeleteMemoryTool(memory=mock_memory)

    result = await tool.execute(memory_id="ghost")

    assert result.success is True
    assert "No active memory" in result.output or "no-op" in result.output.lower()


async def test_delete_memory_empty_id_fails(mock_memory):
    tool = DeleteMemoryTool(memory=mock_memory)
    result = await tool.execute(memory_id="")

    assert result.success is False
    mock_memory.delete.assert_not_awaited()


async def test_delete_memory_repo_exception_returned_as_error(mock_memory):
    mock_memory.delete.side_effect = RuntimeError("DB lock")
    tool = DeleteMemoryTool(memory=mock_memory)

    result = await tool.execute(memory_id="abc")

    assert result.success is False
    assert "DB lock" in result.output


# ---------------------------------------------------------------------------
# UpdateMemoryTool
# ---------------------------------------------------------------------------


async def test_update_memory_content_recomputes_embedding(mock_memory, mock_embedder):
    updated = _entry("nuevo contenido")
    mock_memory.update.return_value = updated
    mock_embedder.embed_passage.return_value = [0.5] * 384

    tool = UpdateMemoryTool(memory=mock_memory, embedder=mock_embedder)
    result = await tool.execute(memory_id="abc-1234", content="nuevo contenido")

    assert result.success is True
    # Embedder recibió el nuevo content
    mock_embedder.embed_passage.assert_awaited_once_with("nuevo contenido")
    # Repo recibió el embedding recomputado
    update_kwargs = mock_memory.update.call_args.kwargs
    assert update_kwargs["content"] == "nuevo contenido"
    assert update_kwargs["embedding"] == [0.5] * 384


async def test_update_memory_tags_only_does_not_touch_embedding(mock_memory, mock_embedder):
    mock_memory.update.return_value = _entry("igual")
    tool = UpdateMemoryTool(memory=mock_memory, embedder=mock_embedder)

    await tool.execute(memory_id="abc", tags=["uno", "dos"])

    mock_embedder.embed_passage.assert_not_awaited()
    update_kwargs = mock_memory.update.call_args.kwargs
    assert update_kwargs["embedding"] is None
    assert update_kwargs["tags"] == ["uno", "dos"]


async def test_update_memory_no_fields_provided_fails(mock_memory, mock_embedder):
    tool = UpdateMemoryTool(memory=mock_memory, embedder=mock_embedder)
    result = await tool.execute(memory_id="abc")

    assert result.success is False
    mock_memory.update.assert_not_awaited()


async def test_update_memory_invalid_relevance_fails(mock_memory, mock_embedder):
    tool = UpdateMemoryTool(memory=mock_memory, embedder=mock_embedder)
    result = await tool.execute(memory_id="abc", relevance=2.5)

    assert result.success is False
    assert "0.0 and 1.0" in result.output
    mock_memory.update.assert_not_awaited()


async def test_update_memory_tags_must_be_list(mock_memory, mock_embedder):
    tool = UpdateMemoryTool(memory=mock_memory, embedder=mock_embedder)
    result = await tool.execute(memory_id="abc", tags="no-soy-lista")

    assert result.success is False
    mock_memory.update.assert_not_awaited()


async def test_update_memory_empty_content_fails(mock_memory, mock_embedder):
    tool = UpdateMemoryTool(memory=mock_memory, embedder=mock_embedder)
    result = await tool.execute(memory_id="abc", content="   ")

    assert result.success is False
    mock_memory.update.assert_not_awaited()


async def test_update_memory_id_not_found_fails(mock_memory, mock_embedder):
    mock_memory.update.return_value = None
    tool = UpdateMemoryTool(memory=mock_memory, embedder=mock_embedder)

    result = await tool.execute(memory_id="ghost", content="nuevo")

    assert result.success is False
    assert "ghost" in result.output
