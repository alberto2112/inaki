"""Tests para el admin REST server — endpoints y auth."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from adapters.inbound.rest.admin.app import create_admin_app


@pytest.fixture
def mock_app_container() -> MagicMock:
    container = MagicMock()
    container.scheduler_service = MagicMock()
    container.scheduler_service.invalidate = AsyncMock()
    container.consolidate_all_agents = MagicMock()
    container.consolidate_all_agents.execute = AsyncMock(return_value="Consolidación completa")
    # agents dict con un agente mock
    agent_container = MagicMock()
    agent_container.run_agent = MagicMock()
    agent_container.run_agent.inspect = AsyncMock(return_value={"pipeline": "ok"})
    container.agents = {"general": agent_container}
    return container


@pytest.fixture
def admin_app(mock_app_container: MagicMock) -> object:
    return create_admin_app(mock_app_container, admin_auth_key="test-secret")


@pytest.fixture
def admin_app_no_auth(mock_app_container: MagicMock) -> object:
    return create_admin_app(mock_app_container, admin_auth_key=None)


# ---------------------------------------------------------------------------
# GET /health — sin auth
# ---------------------------------------------------------------------------


async def test_health_returns_200(admin_app) -> None:
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as ac:
        resp = await ac.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


async def test_health_no_auth_required(admin_app) -> None:
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as ac:
        resp = await ac.get("/health")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /scheduler/reload — requiere auth
# ---------------------------------------------------------------------------


async def test_scheduler_reload_200_with_valid_key(admin_app, mock_app_container) -> None:
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as ac:
        resp = await ac.post("/scheduler/reload", headers={"X-Admin-Key": "test-secret"})
    assert resp.status_code == 200
    mock_app_container.scheduler_service.invalidate.assert_awaited_once()


async def test_scheduler_reload_401_without_key(admin_app) -> None:
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as ac:
        resp = await ac.post("/scheduler/reload")
    assert resp.status_code == 401


async def test_scheduler_reload_401_with_wrong_key(admin_app) -> None:
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as ac:
        resp = await ac.post("/scheduler/reload", headers={"X-Admin-Key": "wrong"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /inspect — requiere auth
# ---------------------------------------------------------------------------


async def test_inspect_200_with_valid_key(admin_app) -> None:
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as ac:
        resp = await ac.post(
            "/inspect",
            json={"agent_id": "general", "mensaje": "hola"},
            headers={"X-Admin-Key": "test-secret"},
        )
    assert resp.status_code == 200


async def test_inspect_401_without_key(admin_app) -> None:
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as ac:
        resp = await ac.post("/inspect", json={"agent_id": "general", "mensaje": "hola"})
    assert resp.status_code == 401


async def test_inspect_404_unknown_agent(admin_app) -> None:
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as ac:
        resp = await ac.post(
            "/inspect",
            json={"agent_id": "nonexistent", "mensaje": "hola"},
            headers={"X-Admin-Key": "test-secret"},
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /consolidate — requiere auth
# ---------------------------------------------------------------------------


async def test_consolidate_200_with_valid_key(admin_app, mock_app_container) -> None:
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as ac:
        resp = await ac.post("/consolidate", headers={"X-Admin-Key": "test-secret"})
    assert resp.status_code == 200
    mock_app_container.consolidate_all_agents.execute.assert_awaited_once()


async def test_consolidate_401_without_key(admin_app) -> None:
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as ac:
        resp = await ac.post("/consolidate")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Auth deshabilitada — sin auth_key configurada → 403
# ---------------------------------------------------------------------------


async def test_protected_endpoint_403_when_no_auth_key_configured(admin_app_no_auth) -> None:
    async with AsyncClient(transport=ASGITransport(app=admin_app_no_auth), base_url="http://test") as ac:
        resp = await ac.post("/scheduler/reload")
    assert resp.status_code == 403
