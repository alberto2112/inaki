"""``LLMConfig.request_delay_seconds`` — throttle del provider con fallback safe.

Reglas:
  - Sin override → default 2.0.
  - Valor >= 0 → se respeta tal cual (0 = throttle desactivado).
  - Valor negativo → clamp a 0.0 (no tiene sentido un delay negativo).
  - Valor no parseable (str no numérico, None, list, dict) → fallback a 2.0.
    Igual criterio que ``timeout_seconds``: el bootstrap del daemon no debe
    morir por un dedazo en el YAML.
"""

from __future__ import annotations

import pytest

from infrastructure.config import LLMConfig


def test_default_is_2() -> None:
    assert LLMConfig().request_delay_seconds == 2.0


@pytest.mark.parametrize("value", [0, 0.5, 1, 2, 5.5, 30])
def test_non_negative_is_kept(value: float) -> None:
    assert LLMConfig(request_delay_seconds=value).request_delay_seconds == float(value)


@pytest.mark.parametrize("value", [-1, -0.5, -100])
def test_negative_clamps_to_zero(value: float) -> None:
    assert LLMConfig(request_delay_seconds=value).request_delay_seconds == 0.0


@pytest.mark.parametrize("value", ["abc", "", "  ", None, [], {}])
def test_malformed_falls_back_to_2(value: object) -> None:
    # Tipos inválidos a propósito para verificar coerción sin raise.
    assert LLMConfig(request_delay_seconds=value).request_delay_seconds == 2.0  # type: ignore[arg-type]


def test_string_numeric_is_coerced() -> None:
    """``float(v)`` acepta strings numéricos — valor válido se respeta."""
    assert LLMConfig(request_delay_seconds="1.5").request_delay_seconds == 1.5  # type: ignore[arg-type]
