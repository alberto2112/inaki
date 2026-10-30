"""Contrato del port ``IBackgroundDelegationQueue``: la operación ``resume`` (REQ-RES-5..7)."""

from __future__ import annotations

import inspect

from inaki.kernel.ports.background_delegation_port import IBackgroundDelegationQueue


class TestResumeEnElPort:
    def test_el_port_declara_resume_async(self) -> None:
        assert inspect.iscoroutinefunction(IBackgroundDelegationQueue.resume)

    def test_firma_de_resume(self) -> None:
        params = inspect.signature(IBackgroundDelegationQueue.resume).parameters

        assert list(params) == [
            "self",
            "task_id",
            "caller_agent_id",
            "answer",
            "target_agent_id",
            "allowed_targets",
        ]
        # target_agent_id y allowed_targets son solo-keyword: no se pasan por posición.
        assert params["target_agent_id"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["allowed_targets"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["target_agent_id"].default is None
        assert params["allowed_targets"].default == ()

    def test_snapshot_inflight_y_cancel_documentan_awaiting_input(self) -> None:
        assert "awaiting_input" in (IBackgroundDelegationQueue.snapshot_inflight.__doc__ or "")
        assert "awaiting_input" in (IBackgroundDelegationQueue.cancel.__doc__ or "")
