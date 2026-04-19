"""
Tests unitarios para KnowledgeSearchTool.

Verifica:
- Búsqueda básica retorna ToolResult con éxito
- Filtro por source (parámetro 'source')
- Source desconocido retorna ToolResult con error (sin excepción)
- Query vacía retorna error sin llamar al orquestador
- top_k se pasa correctamente
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from adapters.outbound.tools.knowledge_search_tool import KnowledgeSearchTool
from core.domain.value_objects.knowledge_chunk import KnowledgeChunk


def _make_chunk(
    source_id: str = "memory",
    content: str = "fragmento de prueba",
    score: float = 0.8,
) -> KnowledgeChunk:
    return KnowledgeChunk(source_id=source_id, content=content, score=score)


def _make_tool(chunks: list[KnowledgeChunk] | None = None, source_ids: list[str] | None = None):
    """Crea KnowledgeSearchTool con mocks del orquestador y embedder."""
    orchestrator = MagicMock()
    orchestrator.source_ids = list(source_ids or ["memory"])
    orchestrator.retrieve_all = AsyncMock(return_value=chunks or [])

    embedder = MagicMock()
    embedder.embed_query = AsyncMock(return_value=[0.1] * 384)

    return KnowledgeSearchTool(orchestrator=orchestrator, embedder=embedder), orchestrator, embedder


class TestBasicQuery:
    """Búsqueda básica sin filtros."""

    async def test_query_basica_retorna_exito(self) -> None:
        chunks = [_make_chunk(content="Iñaki es un asistente")]
        tool, orchestrator, _ = _make_tool(chunks=chunks)

        resultado = await tool.execute(query="asistente")

        assert resultado.success is True
        assert "Iñaki es un asistente" in resultado.output
        orchestrator.retrieve_all.assert_called_once()

    async def test_sin_resultados_retorna_exito_con_mensaje(self) -> None:
        tool, _, _ = _make_tool(chunks=[])

        resultado = await tool.execute(query="algo inexistente")

        assert resultado.success is True
        assert "No relevant results" in resultado.output

    async def test_query_vacia_retorna_error_sin_llamar_orchestrator(self) -> None:
        tool, orchestrator, _ = _make_tool()

        resultado = await tool.execute(query="")

        assert resultado.success is False
        assert resultado.error == "query empty"
        orchestrator.retrieve_all.assert_not_called()

    async def test_query_solo_espacios_retorna_error(self) -> None:
        tool, orchestrator, _ = _make_tool()

        resultado = await tool.execute(query="   ")

        assert resultado.success is False
        orchestrator.retrieve_all.assert_not_called()

    async def test_output_incluye_source_id_y_score(self) -> None:
        chunks = [_make_chunk(source_id="memory", content="test", score=0.75)]
        tool, _, _ = _make_tool(chunks=chunks)

        resultado = await tool.execute(query="test")

        assert "memory" in resultado.output
        assert "0.750" in resultado.output

    async def test_nombre_tool_es_knowledge_search(self) -> None:
        tool, _, _ = _make_tool()
        assert tool.name == "knowledge_search"


class TestSourceFilter:
    """Filtro por source_id."""

    async def test_source_conocido_filtra_correctamente(self) -> None:
        chunks = [
            _make_chunk(source_id="memory", content="memoria"),
            _make_chunk(source_id="docs", content="doc"),
        ]
        tool, _, _ = _make_tool(chunks=chunks, source_ids=["memory", "docs"])

        resultado = await tool.execute(query="test", source="memory")

        assert resultado.success is True
        assert "memoria" in resultado.output
        assert "doc" not in resultado.output

    async def test_source_desconocido_retorna_error_sin_excepcion(self) -> None:
        """Source ID inexistente debe retornar ToolResult con error, nunca lanzar excepción."""
        tool, _, _ = _make_tool(source_ids=["memory"])

        resultado = await tool.execute(query="test", source="fuente-inexistente")

        assert resultado.success is False
        assert resultado.error is not None
        assert "unknown source" in resultado.error
        assert "fuente-inexistente" in resultado.output

    async def test_source_desconocido_lista_fuentes_disponibles(self) -> None:
        tool, _, _ = _make_tool(source_ids=["memory", "docs-proyecto"])

        resultado = await tool.execute(query="test", source="inexistente")

        # Debe listar las fuentes disponibles en el mensaje de error
        assert "memory" in resultado.output or "docs-proyecto" in resultado.output

    async def test_sin_source_consulta_todas_las_fuentes(self) -> None:
        chunks = [
            _make_chunk(source_id="memory", content="memoria"),
            _make_chunk(source_id="docs", content="doc"),
        ]
        tool, orchestrator, _ = _make_tool(chunks=chunks, source_ids=["memory", "docs"])

        resultado = await tool.execute(query="test")

        # Sin filtro → todos los chunks
        assert "memoria" in resultado.output
        assert "doc" in resultado.output


class TestTopK:
    """El parámetro top_k se pasa al orquestador correctamente."""

    async def test_top_k_default_es_cinco(self) -> None:
        tool, orchestrator, _ = _make_tool()

        await tool.execute(query="test")

        call_kwargs = orchestrator.retrieve_all.call_args
        assert call_kwargs.kwargs["top_k"] == 5

    async def test_top_k_personalizado(self) -> None:
        tool, orchestrator, _ = _make_tool()

        await tool.execute(query="test", top_k=3)

        call_kwargs = orchestrator.retrieve_all.call_args
        assert call_kwargs.kwargs["top_k"] == 3

    async def test_top_k_se_clampea_a_maximo_veinte(self) -> None:
        tool, orchestrator, _ = _make_tool()

        await tool.execute(query="test", top_k=999)

        call_kwargs = orchestrator.retrieve_all.call_args
        assert call_kwargs.kwargs["top_k"] == 20

    async def test_top_k_se_clampea_a_minimo_uno(self) -> None:
        tool, orchestrator, _ = _make_tool()

        await tool.execute(query="test", top_k=0)

        call_kwargs = orchestrator.retrieve_all.call_args
        assert call_kwargs.kwargs["top_k"] == 1


class TestErrorHandling:
    """Errores del orquestador o embedder se convierten en ToolResult con error."""

    async def test_excepcion_del_embedder_retorna_error(self) -> None:
        tool, orchestrator, embedder = _make_tool()
        embedder.embed_query = AsyncMock(side_effect=RuntimeError("embedder caído"))

        resultado = await tool.execute(query="test")

        assert resultado.success is False
        assert resultado.retryable is True
        assert "Error searching" in resultado.output

    async def test_excepcion_del_orchestrator_retorna_error(self) -> None:
        tool, orchestrator, _ = _make_tool()
        orchestrator.retrieve_all = AsyncMock(side_effect=Exception("fallo de DB"))

        resultado = await tool.execute(query="test")

        assert resultado.success is False
        assert resultado.retryable is True
