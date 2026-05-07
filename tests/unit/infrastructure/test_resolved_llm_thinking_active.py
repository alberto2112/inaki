"""Truth table de ``ResolvedLLMConfig.thinking_active``.

Regla:
  - ``None`` o cadena vacía → False.
  - ``"low"`` → False (DeepSeek mapea ``low → high``, no aporta granularidad).
  - cualquier otro valor → True.
"""

from __future__ import annotations

import pytest

from infrastructure.config import ResolvedLLMConfig


def _cfg(reasoning_effort: str | None) -> ResolvedLLMConfig:
    return ResolvedLLMConfig(
        provider="deepseek",
        model="deepseek-v4-pro",
        temperature=0.7,
        max_tokens=1024,
        api_key="sk-test",
        reasoning_effort=reasoning_effort,
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, False),
        ("", False),
        ("   ", False),
        ("low", False),
        ("LOW", False),
        ("  low  ", False),
        ("medium", True),
        ("high", True),
        ("max", True),
        ("MAX", True),
        ("custom-future-value", True),
    ],
)
def test_thinking_active_truth_table(value: str | None, expected: bool) -> None:
    assert _cfg(value).thinking_active is expected
