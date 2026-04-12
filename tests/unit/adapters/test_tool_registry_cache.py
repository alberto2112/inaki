"""Tests para integración de ToolRegistry con IEmbeddingCache."""

from unittest.mock import AsyncMock, MagicMock

from adapters.outbound.tools.tool_registry import ToolRegistry


def _make_tool(name: str = "test_tool", description: str = "Herramienta de test") -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.parameters_schema = {"type": "object", "properties": {}}
    return tool


async def test_cache_miss_llama_embed_y_put():
    embedder = AsyncMock()
    embedder.embed_passage.return_value = [0.1] * 384
    cache = AsyncMock()
    cache.get.return_value = None  # miss

    registry = ToolRegistry(embedder, cache=cache, dimension=384)
    registry.register(_make_tool())
    await registry.get_schemas_relevant([0.1] * 384)

    embedder.embed_passage.assert_called_once()
    cache.put.assert_called_once()


async def test_cache_hit_no_llama_embed():
    embedder = AsyncMock()
    embedder.embed_passage.return_value = [0.1] * 384
    cached_embedding = [0.5] * 384
    cache = AsyncMock()
    cache.get.return_value = cached_embedding  # hit

    registry = ToolRegistry(embedder, cache=cache, dimension=384)
    registry.register(_make_tool())
    await registry.get_schemas_relevant([0.1] * 384)

    embedder.embed_passage.assert_not_called()
    cache.put.assert_not_called()
    assert registry._embeddings["test_tool"] == cached_embedding


async def test_descripcion_cambiada_genera_miss():
    embedder = AsyncMock()
    embedder.embed_passage.return_value = [0.1] * 384
    cache = AsyncMock()
    cache.get.return_value = None  # siempre miss

    registry = ToolRegistry(embedder, cache=cache, dimension=384)
    registry.register(_make_tool(description="version 1"))
    await registry.get_schemas_relevant([0.1] * 384)

    # Re-registrar con descripción diferente
    registry._tools.clear()
    registry._embeddings.clear()
    registry._embeddings_ready = False
    registry.register(_make_tool(description="version 2"))
    await registry.get_schemas_relevant([0.1] * 384)

    assert embedder.embed_passage.call_count == 2
    hashes = [call.args[0] for call in cache.get.call_args_list]
    assert hashes[0] != hashes[1]


async def test_sin_cache_comportamiento_original():
    embedder = AsyncMock()
    embedder.embed_passage.return_value = [0.1] * 384

    registry = ToolRegistry(embedder)  # sin cache
    registry.register(_make_tool())
    await registry.get_schemas_relevant([0.1] * 384)

    embedder.embed_passage.assert_called_once()
