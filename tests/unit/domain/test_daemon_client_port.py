"""Tests para el port IDaemonClient — verificación estructural."""

from __future__ import annotations

import inspect

from core.ports.outbound.daemon_client_port import IDaemonClient


def test_idaemon_client_is_protocol() -> None:
    from typing import Protocol

    assert issubclass(IDaemonClient, Protocol)


def test_idaemon_client_has_health_method() -> None:
    assert hasattr(IDaemonClient, "health")


def test_idaemon_client_has_scheduler_reload_method() -> None:
    assert hasattr(IDaemonClient, "scheduler_reload")


def test_idaemon_client_has_inspect_method() -> None:
    assert hasattr(IDaemonClient, "inspect")


def test_idaemon_client_has_consolidate_method() -> None:
    assert hasattr(IDaemonClient, "consolidate")


def test_health_returns_bool_annotation() -> None:
    hints = inspect.get_annotations(IDaemonClient.health, eval_str=True)
    assert hints.get("return") is bool


def test_scheduler_reload_returns_bool_annotation() -> None:
    hints = inspect.get_annotations(IDaemonClient.scheduler_reload, eval_str=True)
    assert hints.get("return") is bool
