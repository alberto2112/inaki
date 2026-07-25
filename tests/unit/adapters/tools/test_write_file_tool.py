"""Tests unitarios de WriteFileTool — modo explícito (create / overwrite / append)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters.outbound.tools.write_file_tool import WriteFileTool


@pytest.fixture
def tool(tmp_path: Path) -> WriteFileTool:
    return WriteFileTool(workspace=tmp_path, containment="strict")


def _payload(result) -> dict:
    return json.loads(result.output)


# -----------------------------
# Modo ausente
# -----------------------------


async def test_sin_modo_crea_fichero_nuevo(tool: WriteFileTool, tmp_path: Path) -> None:
    result = await tool.execute(file_path="nuevo.txt", content="hola")

    assert result.success is True
    assert _payload(result)["mode"] == "create"
    assert (tmp_path / "nuevo.txt").read_text() == "hola"


async def test_sin_modo_sobre_fichero_vacio_lo_trata_como_create(
    tool: WriteFileTool, tmp_path: Path
) -> None:
    f = tmp_path / "vacio.txt"
    f.write_text("", encoding="utf-8")

    result = await tool.execute(file_path="vacio.txt", content="hola")

    assert result.success is True
    assert f.read_text() == "hola"


async def test_sin_modo_sobre_fichero_con_contenido_falla(
    tool: WriteFileTool, tmp_path: Path
) -> None:
    """El bug histórico: sin modo, el fichero actualizado se pegaba al viejo (duplicado)."""
    f = tmp_path / "doc.md"
    f.write_text("linea vieja\n", encoding="utf-8")

    result = await tool.execute(file_path="doc.md", content="linea nueva")

    assert result.success is False
    error = _payload(result)["error"]
    assert "'mode' must be stated explicitly" in error
    # El mensaje enseña la alternativa, no solo el fallo.
    assert "edit_file" in error
    assert "patch_file" in error
    # Y sobre todo: el fichero quedó intacto.
    assert f.read_text() == "linea vieja\n"


# -----------------------------
# Modos explícitos
# -----------------------------


async def test_overwrite_reemplaza_todo(tool: WriteFileTool, tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text("viejo\n", encoding="utf-8")

    result = await tool.execute(file_path="doc.md", content="nuevo", mode="overwrite")

    assert result.success is True
    assert f.read_text() == "nuevo"


async def test_append_conserva_lo_anterior(tool: WriteFileTool, tmp_path: Path) -> None:
    f = tmp_path / "log.txt"
    f.write_text("linea 1", encoding="utf-8")

    result = await tool.execute(file_path="log.txt", content="linea 2", mode="append")

    assert result.success is True
    assert f.read_text() == "linea 1\nlinea 2"


async def test_append_sobre_fichero_inexistente_no_agrega_newline(
    tool: WriteFileTool, tmp_path: Path
) -> None:
    result = await tool.execute(file_path="log.txt", content="linea 1", mode="append")

    assert result.success is True
    assert (tmp_path / "log.txt").read_text() == "linea 1"


async def test_create_sobre_fichero_con_contenido_falla(
    tool: WriteFileTool, tmp_path: Path
) -> None:
    f = tmp_path / "doc.md"
    f.write_text("ya existe\n", encoding="utf-8")

    result = await tool.execute(file_path="doc.md", content="nuevo", mode="create")

    assert result.success is False
    assert "mode='create' cannot be used" in _payload(result)["error"]
    assert f.read_text() == "ya existe\n"


async def test_modo_invalido_falla(tool: WriteFileTool, tmp_path: Path) -> None:
    result = await tool.execute(file_path="doc.md", content="x", mode="truncate")

    assert result.success is False
    assert "Invalid mode" in _payload(result)["error"]
    assert not (tmp_path / "doc.md").exists()


# -----------------------------
# Corte limpio del parámetro legacy
# -----------------------------


async def test_overwrite_legacy_es_rechazado_sin_escribir(
    tool: WriteFileTool, tmp_path: Path
) -> None:
    f = tmp_path / "doc.md"
    f.write_text("viejo\n", encoding="utf-8")

    result = await tool.execute(file_path="doc.md", content="nuevo", overwrite=True)

    assert result.success is False
    assert "'overwrite' parameter no longer exists" in _payload(result)["error"]
    assert f.read_text() == "viejo\n"


async def test_overwrite_legacy_false_tambien_es_rechazado(
    tool: WriteFileTool, tmp_path: Path
) -> None:
    """`overwrite=False` era el default que causaba el append silencioso: no se interpreta."""
    f = tmp_path / "doc.md"
    f.write_text("viejo\n", encoding="utf-8")

    result = await tool.execute(file_path="doc.md", content="nuevo", overwrite=False)

    assert result.success is False
    assert f.read_text() == "viejo\n"


# -----------------------------
# create_dirs (sin regresión)
# -----------------------------


async def test_create_dirs_crea_padres(tool: WriteFileTool, tmp_path: Path) -> None:
    result = await tool.execute(
        file_path="a/b/c.txt", content="hola", create_dirs=True, mode="create"
    )

    assert result.success is True
    assert (tmp_path / "a" / "b" / "c.txt").read_text() == "hola"


async def test_sin_create_dirs_falla_si_falta_el_padre(tool: WriteFileTool, tmp_path: Path) -> None:
    result = await tool.execute(file_path="a/b/c.txt", content="hola", mode="create")

    assert result.success is False
    assert "Parent directory does not exist" in _payload(result)["error"]
