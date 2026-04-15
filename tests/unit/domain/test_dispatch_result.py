"""Tests para DispatchResult — value object de trazabilidad del dispatch."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.domain.value_objects.dispatch_result import DispatchResult


def test_construccion_valida_guarda_ambos_targets() -> None:
    result = DispatchResult(original_target="cli:local", resolved_target="file:///tmp/out.log")
    assert result.original_target == "cli:local"
    assert result.resolved_target == "file:///tmp/out.log"


def test_original_y_resolved_pueden_ser_iguales() -> None:
    result = DispatchResult(original_target="telegram:123", resolved_target="telegram:123")
    assert result.original_target == result.resolved_target == "telegram:123"


def test_no_se_puede_mutar_original_target() -> None:
    result = DispatchResult(original_target="cli:local", resolved_target="null:")
    with pytest.raises((ValidationError, TypeError)):
        result.original_target = "otro"  # type: ignore[misc]


def test_no_se_puede_mutar_resolved_target() -> None:
    result = DispatchResult(original_target="cli:local", resolved_target="null:")
    with pytest.raises((ValidationError, TypeError)):
        result.resolved_target = "otro"  # type: ignore[misc]


def test_ambos_campos_son_requeridos() -> None:
    with pytest.raises(ValidationError):
        DispatchResult(original_target="cli:local")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        DispatchResult(resolved_target="null:")  # type: ignore[call-arg]
