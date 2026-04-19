"""
Tests unitarios para adapters/outbound/knowledge/_chunker.py.

Verifica:
- Ventana deslizante (chunk_size, chunk_overlap)
- Split por headers Markdown + ventana dentro de cada sección
- Texto plano (.txt) → ventana deslizante pura
- PDF via pypdf (mockeado para no depender del archivo real)
- Archivo vacío → lista vacía
- Overlap incorrecto (mayor que chunk_size) → no explota
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from adapters.outbound.knowledge._chunker import (
    _extraer_secciones_markdown,
    _ventana_deslizante,
    chunkear_archivo,
)


# ---------------------------------------------------------------------------
# Tests de _ventana_deslizante
# ---------------------------------------------------------------------------


class TestVentanaDeslizante:
    def test_sin_palabras_devuelve_vacio(self) -> None:
        assert _ventana_deslizante([], chunk_size=5, chunk_overlap=1) == []

    def test_chunk_size_mayor_que_texto(self) -> None:
        """Si hay menos palabras que chunk_size, retorna un solo chunk."""
        palabras = "uno dos tres".split()
        chunks = _ventana_deslizante(palabras, chunk_size=10, chunk_overlap=2)
        assert len(chunks) == 1
        assert chunks[0] == "uno dos tres"

    def test_overlap_correcto(self) -> None:
        """Verifica que el solapamiento sea correcto entre chunks consecutivos."""
        palabras = "a b c d e f g h i j".split()
        chunks = _ventana_deslizante(palabras, chunk_size=4, chunk_overlap=2)
        # stride = 4 - 2 = 2
        # chunk 0: a b c d
        # chunk 1: c d e f
        # chunk 2: e f g h
        # chunk 3: g h i j
        assert chunks[0] == "a b c d"
        assert chunks[1] == "c d e f"
        assert "c" in chunks[1]  # solapamiento con chunk anterior

    def test_overlap_cero(self) -> None:
        """Con overlap 0, los chunks no se solapan."""
        palabras = "a b c d e f".split()
        chunks = _ventana_deslizante(palabras, chunk_size=3, chunk_overlap=0)
        assert chunks == ["a b c", "d e f"]

    def test_overlap_mayor_que_chunk_size_no_explota(self) -> None:
        """stride = max(1, chunk_size - overlap) → no loop infinito."""
        palabras = "a b c d e".split()
        # overlap=10, chunk_size=3 → stride = max(1, 3-10) = max(1, -7) = 1
        chunks = _ventana_deslizante(palabras, chunk_size=3, chunk_overlap=10)
        # stride 1: a b c, b c d, c d e, d e, e
        assert len(chunks) >= 3
        assert all(c.strip() for c in chunks)

    def test_chunks_no_vacios(self) -> None:
        """No debe haber chunks vacíos en el resultado."""
        palabras = "x y z w".split()
        chunks = _ventana_deslizante(palabras, chunk_size=2, chunk_overlap=1)
        assert all(c.strip() for c in chunks)


# ---------------------------------------------------------------------------
# Tests de _extraer_secciones_markdown
# ---------------------------------------------------------------------------


class TestExtraerSeccionesMarkdown:
    def test_texto_sin_headers(self) -> None:
        """Si no hay headers, retorna el texto completo como una sola sección."""
        texto = "Esto es un párrafo sin headers."
        secciones = _extraer_secciones_markdown(texto)
        assert len(secciones) == 1
        assert secciones[0] == texto

    def test_texto_con_headers(self) -> None:
        """Verifica que los headers separen el texto en secciones."""
        texto = "intro\n# Header 1\ncontent 1\n## Header 2\ncontent 2"
        secciones = _extraer_secciones_markdown(texto)
        # sección 0: intro, sección 1: # Header 1..., sección 2: ## Header 2...
        assert len(secciones) == 3
        assert "intro" in secciones[0]
        assert "# Header 1" in secciones[1]
        assert "## Header 2" in secciones[2]

    def test_texto_empieza_con_header(self) -> None:
        """Si el texto empieza con header, no debe haber sección pre-header."""
        texto = "# Header 1\ncontent 1\n## Header 2\ncontent 2"
        secciones = _extraer_secciones_markdown(texto)
        assert len(secciones) == 2
        assert secciones[0].startswith("# Header 1")

    def test_triple_hash_header(self) -> None:
        """Los headers ### también se detectan."""
        texto = "### H3\ncontent"
        secciones = _extraer_secciones_markdown(texto)
        assert len(secciones) == 1
        assert "### H3" in secciones[0]


# ---------------------------------------------------------------------------
# Tests de chunkear_archivo
# ---------------------------------------------------------------------------


class TestChunkearArchivo:
    def test_archivo_vacio_retorna_lista_vacia(self, tmp_path: Path) -> None:
        """Un archivo sin contenido útil retorna []."""
        archivo = tmp_path / "vacio.txt"
        archivo.write_text("", encoding="utf-8")
        chunks = chunkear_archivo(archivo)
        assert chunks == []

    def test_archivo_solo_espacios_retorna_lista_vacia(self, tmp_path: Path) -> None:
        """Un archivo con solo espacios retorna []."""
        archivo = tmp_path / "espacios.txt"
        archivo.write_text("   \n\n  ", encoding="utf-8")
        chunks = chunkear_archivo(archivo)
        assert chunks == []

    def test_archivo_txt_chunkeado_correctamente(self, tmp_path: Path) -> None:
        """Un archivo .txt largo debe producir múltiples chunks."""
        palabras = " ".join([f"palabra{i}" for i in range(200)])
        archivo = tmp_path / "largo.txt"
        archivo.write_text(palabras, encoding="utf-8")
        chunks = chunkear_archivo(archivo, chunk_size=50, chunk_overlap=10)
        assert len(chunks) > 1
        assert all(isinstance(c, str) for c in chunks)
        assert all(c.strip() for c in chunks)

    def test_archivo_md_usa_headers(self, tmp_path: Path) -> None:
        """Un .md con headers debe usar la estrategia de headers."""
        contenido = (
            "# Sección 1\n"
            + " ".join([f"p{i}" for i in range(60)])
            + "\n## Sección 2\n"
            + " ".join([f"q{i}" for i in range(60)])
        )
        archivo = tmp_path / "doc.md"
        archivo.write_text(contenido, encoding="utf-8")
        chunks = chunkear_archivo(archivo, chunk_size=30, chunk_overlap=5)
        # Debe haber chunks de ambas secciones
        todo = " ".join(chunks)
        assert "p0" in todo
        assert "q0" in todo

    def test_archivo_md_vacio_retorna_lista_vacia(self, tmp_path: Path) -> None:
        """Un .md vacío retorna []."""
        archivo = tmp_path / "vacio.md"
        archivo.write_text("", encoding="utf-8")
        assert chunkear_archivo(archivo) == []

    def test_archivo_pdf_usa_pypdf(self, tmp_path: Path) -> None:
        """Un .pdf debe delegar en PdfReader (mockeado)."""
        archivo = tmp_path / "doc.pdf"
        archivo.write_bytes(b"%PDF-1.4 fake")  # contenido no real, mockeado

        mock_pagina = MagicMock()
        mock_pagina.extract_text.return_value = " ".join([f"palabra{i}" for i in range(100)])
        mock_reader = MagicMock()
        mock_reader.pages = [mock_pagina]

        with patch("pypdf.PdfReader", return_value=mock_reader):
            chunks = chunkear_archivo(archivo, chunk_size=30, chunk_overlap=5)

        assert len(chunks) >= 1
        assert all(isinstance(c, str) for c in chunks)

    def test_overlap_preservado_entre_chunks(self, tmp_path: Path) -> None:
        """Las últimas palabras del chunk N deben aparecer al inicio del chunk N+1."""
        palabras = [f"w{i}" for i in range(20)]
        archivo = tmp_path / "overlap.txt"
        archivo.write_text(" ".join(palabras), encoding="utf-8")
        chunks = chunkear_archivo(archivo, chunk_size=6, chunk_overlap=2)

        if len(chunks) >= 2:
            # Las últimas 2 palabras del chunk 0 deben estar en chunk 1
            ultimas_del_0 = chunks[0].split()[-2:]
            primeras_del_1 = chunks[1].split()[:2]
            assert ultimas_del_0 == primeras_del_1
