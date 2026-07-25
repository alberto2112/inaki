"""Tests unitarios de ReadFileTool — foco en el flag opcional line_numbers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters.outbound.tools.read_file_tool import ReadFileTool


@pytest.fixture
def tool(tmp_path: Path) -> ReadFileTool:
    return ReadFileTool(workspace=tmp_path, containment="strict")


def _payload(result) -> dict:
    return json.loads(result.output)


async def test_sin_flag_devuelve_contenido_crudo(tool: ReadFileTool, tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("a\nb\nc\n", encoding="utf-8")

    payload = _payload(await tool.execute(file_path="f.txt"))

    assert payload["content"] == "a\nb\nc\n"
    assert payload["line_numbers"] is False
    assert payload["line_count"] == 3


async def test_line_numbers_prefija_cada_linea(tool: ReadFileTool, tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("a\nb\nc\n", encoding="utf-8")

    payload = _payload(await tool.execute(file_path="f.txt", line_numbers=True))

    assert payload["content"] == "1\ta\n2\tb\n3\tc"
    assert payload["line_numbers"] is True


async def test_line_numbers_alinea_al_ancho_del_mayor(tool: ReadFileTool, tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("\n".join(str(i) for i in range(1, 12)), encoding="utf-8")

    content = _payload(await tool.execute(file_path="f.txt", line_numbers=True))["content"]

    lines = content.split("\n")
    assert lines[0] == " 1\t1"
    assert lines[10] == "11\t11"


async def test_line_numbers_son_absolutos_con_offset(tool: ReadFileTool, tmp_path: Path) -> None:
    """Los números deben poder pasarse tal cual a patch_file, aun paginando."""
    (tmp_path / "f.txt").write_text("a\nb\nc\nd\ne\n", encoding="utf-8")

    payload = _payload(
        await tool.execute(file_path="f.txt", offset=2, max_lines=2, line_numbers=True)
    )

    assert payload["content"] == "3\tc\n4\td"
    assert payload["truncated"] is True
    assert payload["line_count"] == 5


async def test_fichero_vacio_con_flag(tool: ReadFileTool, tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("", encoding="utf-8")

    payload = _payload(await tool.execute(file_path="f.txt", line_numbers=True))

    assert payload["content"] == ""
    assert payload["line_count"] == 0


async def test_paginacion_sin_flag_no_regresiona(tool: ReadFileTool, tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("a\nb\nc\nd\n", encoding="utf-8")

    payload = _payload(await tool.execute(file_path="f.txt", offset=1, max_lines=2))

    assert payload["content"] == "b\nc"
    assert payload["truncated"] is True
