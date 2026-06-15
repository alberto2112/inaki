"""
Integration tests para los comandos CLI de knowledge.

Usa typer.testing.CliRunner para probar la sub-app knowledge sin arrancar el daemon.
El embedder real se mockea para evitar dependencias de ONNX en CI.

Cubre:
- `knowledge index <source-id>` éxito (source configurada)
- `knowledge index <source-id>` falla con exit code 1 cuando source-id desconocido
- `knowledge list` muestra fuentes configuradas
- `knowledge stats <source-id>` muestra estadísticas
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from inaki.cli import app


def _build_global_config_mock(docs_path: Path):
    """Construye un mock de GlobalConfig con una fuente 'document' configurada."""
    from types import SimpleNamespace

    source_cfg = SimpleNamespace(
        id="test-docs",
        type="document",
        enabled=True,
        description="Test documentation",
        path=str(docs_path),
        glob="**/*.md",
        chunk_size=50,
        chunk_overlap=10,
        top_k=3,
        min_score=0.5,
    )

    embedding_cfg = SimpleNamespace(
        provider="e5_onnx",
        model_dirname="/tmp/fake-model",
        dimension=384,
        cache_filename=":memory:",
        model="fake",
    )

    knowledge_cfg = SimpleNamespace(
        enabled=True,
        include_memory=True,
        top_k_per_source=3,
        min_score=0.5,
        max_total_chunks=10,
        token_budget_warn_threshold=4000,
        sources=[source_cfg],
    )

    admin_cfg = SimpleNamespace(
        host="127.0.0.1",
        port=6497,
        auth_key=None,
        chat_timeout=300.0,
    )

    global_cfg = SimpleNamespace(
        app=SimpleNamespace(default_agent="general", log_level="WARNING"),
        llm=SimpleNamespace(provider="openrouter"),
        embedding=embedding_cfg,
        knowledge=knowledge_cfg,
        admin=admin_cfg,
        providers={},
    )
    return global_cfg


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def docs_dir(tmp_path: Path) -> Path:
    """Crea una carpeta temporal con un documento markdown de prueba."""
    d = tmp_path / "docs"
    d.mkdir()
    (d / "intro.md").write_text("# Intro\n" + " ".join([f"w{i}" for i in range(60)]))
    return d


class TestKnowledgeIndex:
    def test_index_source_known_exits_0(self, runner, docs_dir: Path, tmp_path: Path) -> None:
        """index con source-id conocido debe completar con exit code 0."""
        global_cfg = _build_global_config_mock(docs_dir)

        vec = [0.0] * 384
        vec[0] = 1.0

        mock_source = MagicMock()
        mock_source.index = AsyncMock(
            return_value={
                "archivos_procesados": 1,
                "archivos_saltados": 0,
                "chunks_nuevos": 3,
            }
        )

        with (
            patch(
                "inaki.knowledge_cli._load_global_config",
                return_value=global_cfg,
            ),
            patch(
                "inaki.knowledge_cli._build_document_source",
                return_value=mock_source,
            ),
        ):
            result = runner.invoke(app, ["knowledge", "index", "test-docs"])

        assert result.exit_code == 0, result.output
        assert "Done" in result.output

    def test_index_source_unknown_exits_1(self, runner, docs_dir: Path) -> None:
        """index con source-id desconocido debe salir con código 1."""
        global_cfg = _build_global_config_mock(docs_dir)

        with patch(
            "inaki.knowledge_cli._load_global_config",
            return_value=global_cfg,
        ):
            result = runner.invoke(app, ["knowledge", "index", "fuente-inexistente"])

        assert result.exit_code == 1
        assert "unknown source" in result.output

    def test_index_stats_shown_in_output(self, runner, docs_dir: Path) -> None:
        """index debe mostrar las estadísticas de la indexación."""
        global_cfg = _build_global_config_mock(docs_dir)

        mock_source = MagicMock()
        mock_source.index = AsyncMock(
            return_value={
                "archivos_procesados": 2,
                "archivos_saltados": 1,
                "chunks_nuevos": 8,
            }
        )

        with (
            patch(
                "inaki.knowledge_cli._load_global_config",
                return_value=global_cfg,
            ),
            patch(
                "inaki.knowledge_cli._build_document_source",
                return_value=mock_source,
            ),
        ):
            result = runner.invoke(app, ["knowledge", "index", "test-docs"])

        assert result.exit_code == 0
        assert "2" in result.output  # archivos_procesados
        assert "8" in result.output  # chunks_nuevos


class TestKnowledgeList:
    def test_list_shows_configured_sources(self, runner, docs_dir: Path) -> None:
        """list debe mostrar las fuentes configuradas."""
        global_cfg = _build_global_config_mock(docs_dir)

        with patch(
            "inaki.knowledge_cli._load_global_config",
            return_value=global_cfg,
        ):
            result = runner.invoke(app, ["knowledge", "list"])

        assert result.exit_code == 0, result.output
        assert "test-docs" in result.output
        assert "document" in result.output

    def test_list_empty_when_no_sources(self, runner) -> None:
        """list debe indicar que no hay fuentes cuando sources está vacío."""
        from types import SimpleNamespace

        global_cfg = SimpleNamespace(
            knowledge=SimpleNamespace(sources=[]),
        )

        with patch(
            "inaki.knowledge_cli._load_global_config",
            return_value=global_cfg,
        ):
            result = runner.invoke(app, ["knowledge", "list"])

        assert result.exit_code == 0
        assert "No knowledge sources" in result.output


class TestKnowledgeStats:
    def test_stats_source_known(self, runner, docs_dir: Path) -> None:
        """stats de una fuente conocida debe mostrar las estadísticas."""
        global_cfg = _build_global_config_mock(docs_dir)

        mock_source = MagicMock()
        mock_source.get_stats = AsyncMock(
            return_value={
                "source_id": "test-docs",
                "db_path": "/tmp/fake.db",
                "archivos_indexados": 3,
                "chunks_totales": 15,
                "last_indexed_mtime": 1_700_000_000.0,
                "embedding_dimension": 384,
            }
        )

        with (
            patch(
                "inaki.knowledge_cli._load_global_config",
                return_value=global_cfg,
            ),
            patch(
                "inaki.knowledge_cli._build_document_source",
                return_value=mock_source,
            ),
        ):
            result = runner.invoke(app, ["knowledge", "stats", "test-docs"])

        assert result.exit_code == 0, result.output
        assert "test-docs" in result.output
        assert "3" in result.output  # archivos_indexados
        assert "15" in result.output  # chunks_totales
        assert "384" in result.output  # embedding_dimension
        assert "2023" in result.output  # last_indexed renderizado como fecha UTC

    def test_stats_source_unknown_exits_1(self, runner, docs_dir: Path) -> None:
        """stats con source-id desconocido debe salir con código 1."""
        global_cfg = _build_global_config_mock(docs_dir)

        with patch(
            "inaki.knowledge_cli._load_global_config",
            return_value=global_cfg,
        ):
            result = runner.invoke(app, ["knowledge", "stats", "no-existe"])

        assert result.exit_code == 1


class TestKnowledgeIngest:
    def test_ingest_ok(self, runner, docs_dir: Path, tmp_path: Path) -> None:
        """ingest de un archivo existente muestra stored_path y chunks."""
        global_cfg = _build_global_config_mock(docs_dir)
        archivo = tmp_path / "nota.txt"
        archivo.write_text("contenido a ingerir")

        mock_source = MagicMock()
        mock_source.ingest_file = AsyncMock(
            return_value={"stored_path": str(docs_dir / "nota.txt"), "chunks_nuevos": 4}
        )

        with (
            patch("inaki.knowledge_cli._load_global_config", return_value=global_cfg),
            patch("inaki.knowledge_cli._build_document_source", return_value=mock_source),
        ):
            result = runner.invoke(app, ["knowledge", "ingest", "test-docs", str(archivo)])

        assert result.exit_code == 0, result.output
        assert "Done" in result.output
        assert "4" in result.output

    def test_ingest_path_inexistente_exits_2(self, runner, docs_dir: Path) -> None:
        """ingest de un path inexistente → typer valida exists=True (exit 2)."""
        global_cfg = _build_global_config_mock(docs_dir)
        with patch("inaki.knowledge_cli._load_global_config", return_value=global_cfg):
            result = runner.invoke(app, ["knowledge", "ingest", "test-docs", "/no/existe.txt"])
        assert result.exit_code == 2


class TestKnowledgeDocs:
    def test_docs_lists_indexed_files(self, runner, docs_dir: Path) -> None:
        """docs lista los archivos indexados de una fuente."""
        global_cfg = _build_global_config_mock(docs_dir)

        mock_source = MagicMock()
        mock_source.list_files = AsyncMock(
            return_value=[{"file_path": "/docs/a.md", "mtime": 1.0, "chunk_count": 4}]
        )

        with (
            patch("inaki.knowledge_cli._load_global_config", return_value=global_cfg),
            patch("inaki.knowledge_cli._build_document_source", return_value=mock_source),
        ):
            result = runner.invoke(app, ["knowledge", "docs", "test-docs"])

        assert result.exit_code == 0, result.output
        assert "a.md" in result.output


class TestKnowledgeDelete:
    def test_delete_ok(self, runner, docs_dir: Path) -> None:
        """delete de un archivo presente borra los chunks."""
        global_cfg = _build_global_config_mock(docs_dir)

        mock_source = MagicMock()
        mock_source.delete_file = AsyncMock(return_value=3)

        with (
            patch("inaki.knowledge_cli._load_global_config", return_value=global_cfg),
            patch("inaki.knowledge_cli._build_document_source", return_value=mock_source),
        ):
            result = runner.invoke(app, ["knowledge", "delete", "test-docs", "a.md"])

        assert result.exit_code == 0, result.output
        assert "3" in result.output
        mock_source.delete_file.assert_awaited_once_with("a.md", remove_physical=False)

    def test_delete_not_found_exits_1(self, runner, docs_dir: Path) -> None:
        """delete de un archivo no indexado → exit 1."""
        global_cfg = _build_global_config_mock(docs_dir)

        mock_source = MagicMock()
        mock_source.delete_file = AsyncMock(return_value=0)

        with (
            patch("inaki.knowledge_cli._load_global_config", return_value=global_cfg),
            patch("inaki.knowledge_cli._build_document_source", return_value=mock_source),
        ):
            result = runner.invoke(app, ["knowledge", "delete", "test-docs", "fantasma.md"])

        assert result.exit_code == 1
        assert "not found" in result.output

    def test_delete_remove_file_flag(self, runner, docs_dir: Path) -> None:
        """--remove-file propaga remove_physical=True al source."""
        global_cfg = _build_global_config_mock(docs_dir)

        mock_source = MagicMock()
        mock_source.delete_file = AsyncMock(return_value=2)

        with (
            patch("inaki.knowledge_cli._load_global_config", return_value=global_cfg),
            patch("inaki.knowledge_cli._build_document_source", return_value=mock_source),
        ):
            result = runner.invoke(
                app, ["knowledge", "delete", "test-docs", "a.md", "--remove-file"]
            )

        assert result.exit_code == 0, result.output
        mock_source.delete_file.assert_awaited_once_with("a.md", remove_physical=True)
