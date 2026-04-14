"""Integration tests para el admin REST server — ciclo completo request/response."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from adapters.inbound.rest.admin.app import create_admin_app


@pytest.fixture
def app_container() -> MagicMock:
    """AppContainer mock con componentes reales suficientes para integration."""
    container = MagicMock()
    container.scheduler_service = MagicMock()
    container.scheduler_service.invalidate = AsyncMock()
    container.consolidate_all_agents = MagicMock()
    container.consolidate_all_agents.execute = AsyncMock(return_value="3 agentes consolidados")

    agent = MagicMock()
    agent.run_agent = MagicMock()
    agent.run_agent.inspect = AsyncMock(return_value={
        "memories": ["m1"],
        "skills": ["s1"],
        "tools": ["t1"],
    })
    container.agents = {"general": agent, "dev": MagicMock()}
    return container


@pytest.fixture
def admin_app_with_auth(app_container: MagicMock):
    return create_admin_app(app_container, admin_auth_key="integration-key")


# ---------------------------------------------------------------------------
# Health — sin auth
# ---------------------------------------------------------------------------


async def test_health_integration(admin_app_with_auth) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=admin_app_with_auth), base_url="http://test"
    ) as ac:
        resp = await ac.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Scheduler reload — auth completa
# ---------------------------------------------------------------------------


async def test_scheduler_reload_full_cycle(admin_app_with_auth, app_container) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=admin_app_with_auth), base_url="http://test"
    ) as ac:
        resp = await ac.post(
            "/scheduler/reload",
            headers={"X-Admin-Key": "integration-key"},
        )
    assert resp.status_code == 200
    assert resp.json()["reloaded"] is True
    app_container.scheduler_service.invalidate.assert_awaited_once()


async def test_scheduler_reload_wrong_key(admin_app_with_auth) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=admin_app_with_auth), base_url="http://test"
    ) as ac:
        resp = await ac.post(
            "/scheduler/reload",
            headers={"X-Admin-Key": "wrong-key"},
        )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Inspect — con agente válido e inválido
# ---------------------------------------------------------------------------


async def test_inspect_valid_agent(admin_app_with_auth) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=admin_app_with_auth), base_url="http://test"
    ) as ac:
        resp = await ac.post(
            "/inspect",
            json={"agent_id": "general", "mensaje": "test pipeline"},
            headers={"X-Admin-Key": "integration-key"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "memories" in data
    assert "skills" in data


async def test_inspect_invalid_agent(admin_app_with_auth) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=admin_app_with_auth), base_url="http://test"
    ) as ac:
        resp = await ac.post(
            "/inspect",
            json={"agent_id": "nonexistent", "mensaje": "test"},
            headers={"X-Admin-Key": "integration-key"},
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Consolidate
# ---------------------------------------------------------------------------


async def test_consolidate_full_cycle(admin_app_with_auth, app_container) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=admin_app_with_auth), base_url="http://test"
    ) as ac:
        resp = await ac.post(
            "/consolidate",
            headers={"X-Admin-Key": "integration-key"},
        )
    assert resp.status_code == 200
    assert "consolidados" in resp.json()["resultado"]
    app_container.consolidate_all_agents.execute.assert_awaited_once()
