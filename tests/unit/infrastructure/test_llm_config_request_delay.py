"""``LLMConfig.request_delay_seconds`` — throttle del provider.

Reglas:
  - Sin override → default 2.0.
  - Valor >= 0 → se respeta tal cual (``0`` = throttle desactivado).
  - Valor negativo o no parseable → **error de validación**.

CAMBIO DE CRITERIO (Fase 4 del refactor de config): antes un negativo se
clampeaba a ``0`` y un valor no parseable caía al default ``2.0``, con el mismo
argumento que ``timeout_seconds`` — no matar el bootstrap por un dedazo. Se
revierte por la misma razón: un clamp silencioso desactiva el throttle sin que
nadie se entere, y el rate limiter del provider se satura en el primer turno
con varias tool calls.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from infrastructure.config import LLMConfig


def test_default_is_2() -> None:
    assert LLMConfig().request_delay_seconds == 2.0


@pytest.mark.parametrize("value", [0, 0.5, 1, 2, 5.5, 30])
def test_non_negative_is_kept(value: float) -> None:
    assert LLMConfig(request_delay_seconds=value).request_delay_seconds == float(value)


def test_string_numeric_is_coerced() -> None:
    assert LLMConfig(request_delay_seconds="1.5").request_delay_seconds == 1.5  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [-1, -0.5, -100])
def test_negative_es_error(value: float) -> None:
    """Antes clampeaba a 0.0 — es decir, desactivaba el throttle en silencio."""
    with pytest.raises(ValidationError, match="request_delay_seconds"):
        LLMConfig(request_delay_seconds=value)


@pytest.mark.parametrize("value", ["abc", "", "  ", None, [], {}])
def test_malformed_es_error(value: object) -> None:
    with pytest.raises(ValidationError, match="request_delay_seconds"):
        LLMConfig(request_delay_seconds=value)  # type: ignore[arg-type]
