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


# ---------------------------------------------------------------------------
# DaemonError — excepciones del daemon client
# ---------------------------------------------------------------------------

from core.domain.errors import (
    DaemonClientError,
    DaemonError,
    DaemonNotRunningError,
    DaemonTimeoutError,
)


def test_daemon_error_is_inaki_error() -> None:
    exc = DaemonError("test")
    assert isinstance(exc, IñakiError)


def test_daemon_not_running_error_is_daemon_error() -> None:
    exc = DaemonNotRunningError()
    assert isinstance(exc, DaemonError)


def test_daemon_not_running_error_message_rioplatense() -> None:
    exc = DaemonNotRunningError()
    assert "inaki daemon" in str(exc).lower()


def test_daemon_not_running_error_custom_message() -> None:
    exc = DaemonNotRunningError("custom msg")
    assert "custom msg" in str(exc)


def test_daemon_timeout_error_is_daemon_error() -> None:
    exc = DaemonTimeoutError()
    assert isinstance(exc, DaemonError)


def test_daemon_timeout_error_message() -> None:
    exc = DaemonTimeoutError()
    msg = str(exc).lower()
    assert "timeout" in msg or "tiempo" in msg


def test_daemon_client_error_is_daemon_error() -> None:
    exc = DaemonClientError(status_code=500, detail="Internal")
    assert isinstance(exc, DaemonError)


def test_daemon_client_error_stores_status_code() -> None:
    exc = DaemonClientError(status_code=401, detail="No autorizado")
    assert exc.status_code == 401


def test_daemon_client_error_stores_detail() -> None:
    exc = DaemonClientError(status_code=403, detail="Forbidden")
    assert exc.detail == "Forbidden"


def test_daemon_client_error_str_includes_status() -> None:
    exc = DaemonClientError(status_code=500, detail="Error interno")
    assert "500" in str(exc)


# ---------------------------------------------------------------------------
# Tarea 2.1 — UnknownAgentError y DaemonAuthError
# ---------------------------------------------------------------------------

from core.domain.errors import UnknownAgentError, DaemonAuthError  # noqa: E402


def test_unknown_agent_error_es_subclase_de_daemon_client_error() -> None:
    exc = UnknownAgentError(agent_id="dev")
    assert isinstance(exc, DaemonClientError)


def test_unknown_agent_error_es_subclase_de_daemon_error() -> None:
    exc = UnknownAgentError(agent_id="dev")
    assert isinstance(exc, DaemonError)


def test_unknown_agent_error_incluye_agent_id_en_mensaje() -> None:
    exc = UnknownAgentError(agent_id="mi-agente")
    assert "mi-agente" in str(exc)


def test_unknown_agent_error_almacena_agent_id() -> None:
    exc = UnknownAgentError(agent_id="general")
    assert exc.agent_id == "general"


def test_daemon_auth_error_es_subclase_de_daemon_client_error() -> None:
    exc = DaemonAuthError()
    assert isinstance(exc, DaemonClientError)


def test_daemon_auth_error_es_subclase_de_daemon_error() -> None:
    exc = DaemonAuthError()
    assert isinstance(exc, DaemonError)


def test_daemon_auth_error_mensaje_hace_referencia_a_auth_key() -> None:
    exc = DaemonAuthError()
    assert "auth_key" in str(exc).lower() or "admin-key" in str(exc).lower() or "x-admin-key" in str(exc).lower()
