"""``LLMConfig.timeout_seconds`` — un valor mal escrito es un error, no un default.

Reglas:
  - Sin override → default 60.
  - Valor positivo (int o string numérico) → se respeta tal cual.
  - Valor ``<= 0`` o no parseable → **error de validación**.

CAMBIO DE CRITERIO (Fase 4 del refactor de config, `docs/plans/config-refactor-plan.md`).
Hasta acá el campo se sanitizaba al fallback de 60s con la regla explícita de
"que el bootstrap del daemon no muera por un dedazo en el YAML". El problema de
esa red es que ``timeout_seconds: "sesenta"`` corría con 60 **sin decir nada**:
el operador creía haber configurado 300s para thinking mode y no lo había hecho.
Un default silencioso que contradice lo escrito es peor que un arranque que
falla nombrando la línea a corregir.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from infrastructure.config import LLMConfig


def test_default_is_60() -> None:
    assert LLMConfig().timeout_seconds == 60


@pytest.mark.parametrize("value", [180, 300, 1, 60])
def test_valid_positive_int_is_kept(value: int) -> None:
    assert LLMConfig(timeout_seconds=value).timeout_seconds == value


def test_string_numeric_is_coerced() -> None:
    """El YAML puede entregar un número como string: sigue siendo válido."""
    assert LLMConfig(timeout_seconds="180").timeout_seconds == 180  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [0, -1, -100])
def test_zero_or_negative_es_error(value: int) -> None:
    """Un timeout de 0 o negativo no tiene semántica: es un dedazo."""
    with pytest.raises(ValidationError, match="timeout_seconds"):
        LLMConfig(timeout_seconds=value)


@pytest.mark.parametrize("value", ["abc", "", "  ", None, [], {}])
def test_malformed_es_error(value: object) -> None:
    """Antes caía a 60 en silencio; ahora nombra el campo culpable."""
    with pytest.raises(ValidationError, match="timeout_seconds"):
        LLMConfig(timeout_seconds=value)  # type: ignore[arg-type]
