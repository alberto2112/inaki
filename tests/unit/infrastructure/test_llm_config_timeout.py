"""``LLMConfig.timeout_seconds`` con fallback safe a 60s.

Reglas:
  - Sin override → default 60.
  - Valor positivo → se respeta tal cual.
  - Valor inválido (``<= 0``, no parseable a int, str vacío) → fallback a 60.
"""

from __future__ import annotations

import pytest

from infrastructure.config import LLMConfig


def test_default_is_60() -> None:
    assert LLMConfig().timeout_seconds == 60


@pytest.mark.parametrize("value", [180, 300, 1, 60])
def test_valid_positive_int_is_kept(value: int) -> None:
    assert LLMConfig(timeout_seconds=value).timeout_seconds == value


@pytest.mark.parametrize("value", [0, -1, -100])
def test_zero_or_negative_falls_back_to_60(value: int) -> None:
    assert LLMConfig(timeout_seconds=value).timeout_seconds == 60


@pytest.mark.parametrize("value", ["abc", "", "  ", None, [], {}])
def test_malformed_falls_back_to_60(value: object) -> None:
    """Strings no-numéricos, listas, dicts, None → fallback. NO raise.

    El user dijo: "60s por defecto si la config no está definida o mal definida".
    Nada de fail-fast acá: priorizamos que el bootstrap del daemon no muera
    por un dedazo en el YAML.
    """
    assert LLMConfig(timeout_seconds=value).timeout_seconds == 60


def test_string_numeric_is_coerced() -> None:
    """``int(v)`` acepta strings numéricos — valor positivo se respeta."""
    assert LLMConfig(timeout_seconds="180").timeout_seconds == 180
