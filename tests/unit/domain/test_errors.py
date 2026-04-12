"""Tests para las excepciones de dominio."""

from __future__ import annotations

import pytest

from core.domain.errors import IñakiError, ToolLoopMaxIterationsError


def test_tool_loop_max_iterations_error_instantiation() -> None:
    exc = ToolLoopMaxIterationsError(last_response="some LLM response")
    assert exc.last_response == "some LLM response"


def test_tool_loop_max_iterations_error_attribute_survives_raise() -> None:
    with pytest.raises(ToolLoopMaxIterationsError) as exc_info:
        raise ToolLoopMaxIterationsError("final text here")
    assert exc_info.value.last_response == "final text here"


def test_tool_loop_max_iterations_error_is_inaki_error() -> None:
    exc = ToolLoopMaxIterationsError("x")
    assert isinstance(exc, IñakiError)


def test_tool_loop_max_iterations_error_is_exception() -> None:
    exc = ToolLoopMaxIterationsError("x")
    assert isinstance(exc, Exception)


def test_tool_loop_max_iterations_error_str_includes_last_response() -> None:
    exc = ToolLoopMaxIterationsError("the-last-response")
    assert "the-last-response" in str(exc)


def test_tool_loop_max_iterations_error_empty_response() -> None:
    exc = ToolLoopMaxIterationsError("")
    assert exc.last_response == ""
