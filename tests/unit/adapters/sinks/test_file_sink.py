"""Tests para FileSink — append a archivo con timestamp ISO."""

from __future__ import annotations

from pathlib import Path

import pytest

from adapters.outbound.sinks.file_sink import FileSink
from core.domain.value_objects.dispatch_result import DispatchResult


def test_file_sink_tiene_prefix_file() -> None:
    assert FileSink.prefix == "file"


async def test_file_sink_escribe_en_el_path(tmp_path: Path) -> None:
    destino = tmp_path / "out.log"
    sink = FileSink()
    await sink.send(f"file://{destino}", "hola mundo")
    assert destino.exists()
    contenido = destino.read_text()
    assert "hola mundo" in contenido


async def test_file_sink_crea_directorio_padre_si_no_existe(tmp_path: Path) -> None:
    destino = tmp_path / "sub" / "dir" / "out.log"
    sink = FileSink()
    await sink.send(f"file://{destino}", "texto")
    assert destino.exists()


async def test_file_sink_append_no_sobrescribe(tmp_path: Path) -> None:
    destino = tmp_path / "out.log"
    sink = FileSink()
    await sink.send(f"file://{destino}", "primero")
    await sink.send(f"file://{destino}", "segundo")
    contenido = destino.read_text()
    assert "primero" in contenido
    assert "segundo" in contenido


async def test_file_sink_retorna_dispatch_result_con_target(tmp_path: Path) -> None:
    destino = tmp_path / "out.log"
    target = f"file://{destino}"
    sink = FileSink()
    result = await sink.send(target, "x")
    assert isinstance(result, DispatchResult)
    assert result.original_target == target
    assert result.resolved_target == target


async def test_file_sink_linea_contiene_timestamp_iso(tmp_path: Path) -> None:
    destino = tmp_path / "out.log"
    sink = FileSink()
    await sink.send(f"file://{destino}", "mensaje")
    linea = destino.read_text().strip()
    # Formato: "2026-04-15T... | ... | mensaje"
    assert linea.startswith("20")  # año
    assert "T" in linea.split("|")[0]


async def test_file_sink_target_sin_prefix_file_lanza_valueerror() -> None:
    sink = FileSink()
    with pytest.raises(ValueError, match="file://"):
        await sink.send("telegram:123", "x")
