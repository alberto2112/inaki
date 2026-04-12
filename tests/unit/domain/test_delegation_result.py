"""Tests para DelegationResult — value object del contrato de delegación."""

from __future__ import annotations

import json

import pytest

from core.domain.value_objects.delegation_result import DelegationResult


def test_required_fields_success() -> None:
    result = DelegationResult(status="success", summary="task done")
    assert result.status == "success"
    assert result.summary == "task done"


def test_required_fields_failed() -> None:
    result = DelegationResult(status="failed", summary="something went wrong")
    assert result.status == "failed"
    assert result.summary == "something went wrong"


def test_optional_details_defaults_to_none() -> None:
    result = DelegationResult(status="success", summary="ok")
    assert result.details is None


def test_optional_reason_defaults_to_none() -> None:
    result = DelegationResult(status="success", summary="ok")
    assert result.reason is None


def test_details_when_provided() -> None:
    result = DelegationResult(status="failed", summary="parse error", details="raw text here")
    assert result.details == "raw text here"


def test_reason_when_provided() -> None:
    result = DelegationResult(status="failed", summary="not allowed", reason="target_not_allowed")
    assert result.reason == "target_not_allowed"


def test_all_fields_set() -> None:
    result = DelegationResult(
        status="failed",
        summary="child failed",
        details="exception traceback",
        reason="child_exception:ValueError",
    )
    assert result.status == "failed"
    assert result.summary == "child failed"
    assert result.details == "exception traceback"
    assert result.reason == "child_exception:ValueError"


def test_model_dump_roundtrip() -> None:
    result = DelegationResult(status="success", summary="all done", details=None, reason=None)
    d = result.model_dump()
    assert d["status"] == "success"
    assert d["summary"] == "all done"
    assert d["details"] is None
    assert d["reason"] is None


def test_model_dump_json_roundtrip() -> None:
    result = DelegationResult(
        status="success",
        summary="completed",
        details="some detail",
        reason=None,
    )
    raw = result.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["status"] == "success"
    assert parsed["summary"] == "completed"
    assert parsed["details"] == "some detail"
    assert parsed["reason"] is None


def test_model_validate_from_dict() -> None:
    data = {"status": "failed", "summary": "nope", "reason": "unknown_agent"}
    result = DelegationResult.model_validate(data)
    assert result.status == "failed"
    assert result.reason == "unknown_agent"
    assert result.details is None


def test_missing_required_field_status_raises() -> None:
    with pytest.raises(Exception):
        DelegationResult(summary="missing status")  # type: ignore[call-arg]


def test_missing_required_field_summary_raises() -> None:
    with pytest.raises(Exception):
        DelegationResult(status="success")  # type: ignore[call-arg]
