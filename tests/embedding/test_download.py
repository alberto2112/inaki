"""``descargar_modelo_e5``: idempotente, atómica por fichero y con progreso inyectable."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from inaki.embedding.download import (
    FICHEROS_MODELO_E5,
    descargar_modelo_e5,
    modelo_e5_completo,
)

_BASE = "https://huggingface.co/intfloat/multilingual-e5-small/resolve/main"


def _mockear(router: Any, *, model: bytes = b"onnx", tok: bytes = b"{}") -> None:
    router.get(f"{_BASE}/onnx/model.onnx").mock(
        return_value=httpx.Response(200, content=model, headers={"content-length": str(len(model))})
    )
    router.get(f"{_BASE}/tokenizer.json").mock(return_value=httpx.Response(200, content=tok))


@respx.mock
def test_descarga_los_dos_ficheros_y_no_deja_parciales(tmp_path: Path) -> None:
    _mockear(respx, model=b"x" * 3000)
    dest = tmp_path / "e5-small"
    llamadas: list[tuple[str, int, int | None]] = []

    bajados = descargar_modelo_e5(
        dest, progreso=lambda n, r, t: llamadas.append((n, r, t)), client=httpx.Client()
    )

    assert [p.name for p in bajados] == [n for n, _ in FICHEROS_MODELO_E5]
    assert (dest / "model.onnx").read_bytes() == b"x" * 3000
    assert (dest / "tokenizer.json").read_bytes() == b"{}"
    assert not list(dest.glob("*.part"))
    assert modelo_e5_completo(dest)
    # El progreso informa bytes recibidos y el total que anuncia el servidor.
    assert llamadas[0][0] == "model.onnx" and llamadas[0][2] == 3000
    assert llamadas[-1] == ("tokenizer.json", 2, 2)


@respx.mock
def test_es_idempotente_sobre_ficheros_presentes(tmp_path: Path) -> None:
    _mockear(respx)
    dest = tmp_path / "e5-small"
    dest.mkdir()
    (dest / "model.onnx").write_bytes(b"ya estaba")

    bajados = descargar_modelo_e5(dest, client=httpx.Client())

    assert [p.name for p in bajados] == ["tokenizer.json"]
    assert (dest / "model.onnx").read_bytes() == b"ya estaba"
    assert respx.get(f"{_BASE}/onnx/model.onnx").call_count == 0


@respx.mock
def test_un_error_http_propaga_y_no_deja_el_fichero_destino(tmp_path: Path) -> None:
    respx.get(f"{_BASE}/onnx/model.onnx").mock(return_value=httpx.Response(503))
    dest = tmp_path / "e5-small"

    with pytest.raises(httpx.HTTPStatusError):
        descargar_modelo_e5(dest, client=httpx.Client())

    assert not (dest / "model.onnx").exists()
    assert not modelo_e5_completo(dest)
