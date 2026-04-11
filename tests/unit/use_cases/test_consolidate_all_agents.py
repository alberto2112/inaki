"""Tests unitarios para ConsolidateAllAgentsUseCase."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.domain.errors import ConsolidationError
from core.use_cases.consolidate_all_agents import ConsolidateAllAgentsUseCase


def _mock_uc(return_value: str = "✓ 1 recuerdo") -> AsyncMock:
    mock = AsyncMock()
    mock.execute = AsyncMock(return_value=return_value)
    return mock


async def test_empty_registry_returns_friendly_message() -> None:
    uc = ConsolidateAllAgentsUseCase(enabled_agents={}, delay_seconds=0)
    msg = await uc.execute()
    assert "ningún agente" in msg.lower() or "no hay agentes" in msg.lower()


async def test_iterates_all_enabled_agents_in_order() -> None:
    order: list[str] = []

    def _make(agent_id: str) -> AsyncMock:
        mock = AsyncMock()

        async def _run() -> str:
            order.append(agent_id)
            return f"{agent_id} ok"

        mock.execute = AsyncMock(side_effect=_run)
        return mock

    agents = {
        "general": _make("general"),
        "dev": _make("dev"),
    }
    uc = ConsolidateAllAgentsUseCase(enabled_agents=agents, delay_seconds=0)

    result = await uc.execute()

    assert order == ["general", "dev"]
    assert "general" in result
    assert "dev" in result


async def test_one_agent_failure_does_not_stop_the_rest() -> None:
    failing = AsyncMock()
    failing.execute = AsyncMock(side_effect=ConsolidationError("boom"))
    ok = _mock_uc()

    uc = ConsolidateAllAgentsUseCase(
        enabled_agents={"fails": failing, "works": ok},
        delay_seconds=0,
    )

    result = await uc.execute()

    failing.execute.assert_awaited_once()
    ok.execute.assert_awaited_once()
    assert "✗ fails" in result
    assert "✓ works" in result


async def test_unexpected_exception_is_caught_per_agent() -> None:
    boom = AsyncMock()
    boom.execute = AsyncMock(side_effect=RuntimeError("explota"))
    ok = _mock_uc()

    uc = ConsolidateAllAgentsUseCase(
        enabled_agents={"boom": boom, "ok": ok},
        delay_seconds=0,
    )

    result = await uc.execute()

    assert "✗ boom" in result
    assert "✓ ok" in result


async def test_delay_between_agents_invoked(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("core.use_cases.consolidate_all_agents.asyncio.sleep", _fake_sleep)

    agents = {
        "a": _mock_uc(),
        "b": _mock_uc(),
        "c": _mock_uc(),
    }
    uc = ConsolidateAllAgentsUseCase(enabled_agents=agents, delay_seconds=3)

    await uc.execute()

    # Sleep between a→b y b→c, NO después del último
    assert sleeps == [3, 3]


async def test_no_delay_between_agents_when_zero() -> None:
    agents = {"a": _mock_uc(), "b": _mock_uc()}
    uc = ConsolidateAllAgentsUseCase(enabled_agents=agents, delay_seconds=0)

    # No debe romper ni dormir
    result = await uc.execute()
    assert "✓ a" in result
    assert "✓ b" in result
