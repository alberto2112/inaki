"""
Tests unitarios para KnowledgeAdminTool.

Verifica el ruteo por 'operation', la validación de params requeridos y el mapeo
de errores de dominio a ToolResult(success=False).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from adapters.outbound.tools.knowledge_admin_tool import KnowledgeAdminTool
from core.domain.errors import KnowledgeError


def _make_uc() -> MagicMock:
    uc = MagicMock()
    uc.ingest = AsyncMock(
        return_value={"source_id": "docs", "chunks_nuevos": 7, "stored_path": "/docs/x.md"}
    )
    uc.reindex = AsyncMock(
        return_value={
            "source_id": "docs",
            "archivos_procesados": 1,
            "archivos_saltados": 0,
            "chunks_nuevos": 5,
        }
    )
    uc.list_documents = AsyncMock(
        return_value=[{"file_path": "/docs/a.md", "mtime": 1.0, "chunk_count": 4}]
    )
    uc.stats = AsyncMock(
        return_value={
            "source_id": "docs",
            "archivos_indexados": 2,
            "chunks_totales": 10,
            "embedding_dimension": 384,
        }
    )
    uc.delete_document = AsyncMock(
        return_value={"source_id": "docs", "file_path": "a.md", "chunks_borrados": 3}
    )
    uc.list_sources = MagicMock(return_value=[{"source_id": "docs", "description": "inbox"}])
    return uc


class TestRuteo:
    async def test_ingest_ok(self) -> None:
        uc = _make_uc()
        tool = KnowledgeAdminTool(manage_knowledge=uc)
        res = await tool.execute(operation="ingest", path="/tmp/x.md")
        assert res.success
        assert "7 chunk" in res.output
        uc.ingest.assert_awaited_once()
        # el path llega como Path al use case
        args, kwargs = uc.ingest.call_args
        assert args[0] == Path("/tmp/x.md")

    async def test_ingest_sin_path_falla(self) -> None:
        tool = KnowledgeAdminTool(manage_knowledge=_make_uc())
        res = await tool.execute(operation="ingest")
        assert not res.success
        assert "path" in res.output.lower()

    async def test_reindex_ok(self) -> None:
        uc = _make_uc()
        tool = KnowledgeAdminTool(manage_knowledge=uc)
        res = await tool.execute(operation="reindex", source="docs")
        assert res.success
        uc.reindex.assert_awaited_once_with(source_id="docs")

    async def test_list_ok(self) -> None:
        tool = KnowledgeAdminTool(manage_knowledge=_make_uc())
        res = await tool.execute(operation="list")
        assert res.success
        assert "a.md" in res.output

    async def test_stats_ok(self) -> None:
        tool = KnowledgeAdminTool(manage_knowledge=_make_uc())
        res = await tool.execute(operation="stats")
        assert res.success
        assert "10" in res.output

    async def test_delete_ok(self) -> None:
        uc = _make_uc()
        tool = KnowledgeAdminTool(manage_knowledge=uc)
        res = await tool.execute(operation="delete", file_path="a.md", remove_file=True)
        assert res.success
        uc.delete_document.assert_awaited_once_with(
            "a.md", source_id=None, remove_physical=True
        )

    async def test_delete_sin_file_path_falla(self) -> None:
        tool = KnowledgeAdminTool(manage_knowledge=_make_uc())
        res = await tool.execute(operation="delete")
        assert not res.success
        assert "file_path" in res.output.lower()

    async def test_sources_ok(self) -> None:
        tool = KnowledgeAdminTool(manage_knowledge=_make_uc())
        res = await tool.execute(operation="sources")
        assert res.success
        assert "docs" in res.output


class TestErrores:
    async def test_operation_requerida(self) -> None:
        tool = KnowledgeAdminTool(manage_knowledge=_make_uc())
        res = await tool.execute()
        assert not res.success

    async def test_operation_desconocida(self) -> None:
        tool = KnowledgeAdminTool(manage_knowledge=_make_uc())
        res = await tool.execute(operation="frobnicate")
        assert not res.success
        assert "Unknown operation" in res.output

    async def test_knowledge_error_se_mapea(self) -> None:
        uc = _make_uc()
        uc.reindex = AsyncMock(side_effect=KnowledgeError("no hay fuentes"))
        tool = KnowledgeAdminTool(manage_knowledge=uc)
        res = await tool.execute(operation="reindex")
        assert not res.success
        assert "no hay fuentes" in res.output

    async def test_file_not_found_se_mapea(self) -> None:
        uc = _make_uc()
        uc.ingest = AsyncMock(side_effect=FileNotFoundError("/tmp/x.md"))
        tool = KnowledgeAdminTool(manage_knowledge=uc)
        res = await tool.execute(operation="ingest", path="/tmp/x.md")
        assert not res.success
        assert "File not found" in res.output
