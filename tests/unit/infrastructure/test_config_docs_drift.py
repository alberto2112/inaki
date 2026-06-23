"""Test de drift: `docs/config-reference.md` debe estar sincronizado con el schema.

Regenera la referencia desde el schema Pydantic y la compara con el archivo
committeado. Si falla, el schema cambió y nadie regeneró la doc: correr
`inaki gen-docs`. Este test es el que GARANTIZA que la doc de referencia no
vuelva a quedar obsoleta (el drift `memory.*` que motivó todo esto).
"""

from __future__ import annotations

from pathlib import Path

from infrastructure.config_docs import generate_config_reference

_REFERENCE = Path(__file__).resolve().parents[3] / "docs" / "config-reference.md"


def test_config_reference_no_drift() -> None:
    generado = generate_config_reference()
    actual = _REFERENCE.read_text(encoding="utf-8")
    assert generado == actual, (
        "docs/config-reference.md está desincronizado del schema de config. "
        "Regeneralo con `inaki gen-docs`."
    )
