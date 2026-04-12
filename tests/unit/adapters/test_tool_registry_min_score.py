"""Tests para filtrado por min_score en ToolRegistry.get_schemas_relevant()."""

from unittest.mock import AsyncMock, MagicMock, patch

from adapters.outbound.tools.tool_registry import ToolRegistry


def _make_tool(name: str, description: str = "desc") -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.parameters_schema = {"type": "object", "properties": {}}
    return tool


def _registry_with_tools(*names: str) -> ToolRegistry:
    """Crea un registry con tools y embeddings pre-cargados (sin llamar al embedder)."""
    embedder = AsyncMock()
    registry = ToolRegistry(embedder)
    for name in names:
        registry.register(_make_tool(name))
    return registry


async def test_min_score_filtra_tools_por_debajo_del_umbral():
    registry = _registry_with_tools("alta", "media", "baja")
    # Pre-cargar embeddings para evitar llamar al embedder real
    registry._embeddings = {"alta": [1.0], "media": [1.0], "baja": [1.0]}
    registry._embeddings_ready = True

    scores = {"alta": 0.9, "media": 0.5, "baja": 0.1}

    with patch(
        "adapters.outbound.tools.tool_registry.cosine_similarity",
        side_effect=lambda q, emb: scores[
            next(n for n, e in registry._embeddings.items() if e is emb)
        ],
    ):
        result = await registry.get_schemas_relevant(
            [1.0], top_k=10, min_score=0.4
        )

    nombres = {s["function"]["name"] for s in result}
    assert "alta" in nombres
    assert "media" in nombres
    assert "baja" not in nombres


async def test_min_score_cero_no_filtra():
    registry = _registry_with_tools("a", "b")
    registry._embeddings = {"a": [1.0], "b": [1.0]}
    registry._embeddings_ready = True

    with patch(
        "adapters.outbound.tools.tool_registry.cosine_similarity",
        return_value=0.05,
    ):
        result = await registry.get_schemas_relevant(
            [1.0], top_k=10, min_score=0.0
        )

    assert len(result) == 2


async def test_min_score_combina_con_top_k():
    registry = _registry_with_tools("a", "b", "c")
    registry._embeddings = {"a": [1.0], "b": [1.0], "c": [1.0]}
    registry._embeddings_ready = True

    scores = {"a": 0.9, "b": 0.7, "c": 0.6}

    with patch(
        "adapters.outbound.tools.tool_registry.cosine_similarity",
        side_effect=lambda q, emb: scores[
            next(n for n, e in registry._embeddings.items() if e is emb)
        ],
    ):
        result = await registry.get_schemas_relevant(
            [1.0], top_k=1, min_score=0.5
        )

    # Las 3 pasan min_score=0.5, pero top_k=1 limita a 1
    assert len(result) == 1


async def test_min_score_alto_devuelve_vacio():
    registry = _registry_with_tools("a")
    registry._embeddings = {"a": [1.0]}
    registry._embeddings_ready = True

    with patch(
        "adapters.outbound.tools.tool_registry.cosine_similarity",
        return_value=0.3,
    ):
        result = await registry.get_schemas_relevant(
            [1.0], top_k=10, min_score=0.9
        )

    assert result == []
