"""Tests para el admin REST server — endpoints y auth."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from adapters.inbound.rest.admin.app import create_admin_app
from core.use_cases.run_agent import AgentInfoDTO, InspectResult


def _dummy_inspect_result() -> InspectResult:
    return InspectResult(
        user_input="hola",
        memory_digest="",
        all_skills=[],
        selected_skills=[],
        skills_routing_active=False,
        selected_skill_scores=[],
        all_tool_schemas=[],
        selected_tool_schemas=[],
        tools_routing_active=False,
        selected_tool_scores=[],
        system_prompt="",
    )


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
    agent_container.run_agent.inspect = AsyncMock(return_value=_dummy_inspect_result())
    agent_container.run_agent.get_agent_info.return_value = AgentInfoDTO(
        id="general", name="Inaki", description="Asistente general"
    )
    agent_container.consolidate_memory = MagicMock()
    agent_container.consolidate_memory.execute = AsyncMock(return_value="Consolidado 'general'")
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


async def test_consolidate_body_sin_agent_id_consolida_todos(admin_app, mock_app_container) -> None:
    """POST /consolidate con body vacío (agent_id ausente) → consolida todos."""
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as ac:
        resp = await ac.post("/consolidate", json={}, headers={"X-Admin-Key": "test-secret"})
    assert resp.status_code == 200
    mock_app_container.consolidate_all_agents.execute.assert_awaited_once()


async def test_consolidate_con_agent_id_consolida_solo_ese(admin_app, mock_app_container) -> None:
    """POST /consolidate con agent_id → consolida SOLO ese agente, no todos.

    Porteado del POST /consolidate de la superficie per-agente eliminada.
    """
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as ac:
        resp = await ac.post(
            "/consolidate", json={"agent_id": "general"}, headers={"X-Admin-Key": "test-secret"}
        )
    assert resp.status_code == 200
    assert resp.json()["resultado"] == "Consolidado 'general'"
    agent_container = mock_app_container.agents["general"]
    agent_container.consolidate_memory.execute.assert_awaited_once()
    mock_app_container.consolidate_all_agents.execute.assert_not_called()


async def test_consolidate_agent_id_desconocido_404(admin_app) -> None:
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as ac:
        resp = await ac.post(
            "/consolidate", json={"agent_id": "ghost"}, headers={"X-Admin-Key": "test-secret"}
        )
    assert resp.status_code == 404


async def test_consolidate_memoria_desactivada_503(admin_app, mock_app_container) -> None:
    """agent_id con memory.enabled=false (consolidate_memory=None) → 503."""
    mock_app_container.agents["general"].consolidate_memory = None
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as ac:
        resp = await ac.post(
            "/consolidate", json={"agent_id": "general"}, headers={"X-Admin-Key": "test-secret"}
        )
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /admin/agent/info — requiere auth
# ---------------------------------------------------------------------------


async def test_agent_info_200_con_metadata(admin_app) -> None:
    """GET /admin/agent/info → id, name y description del agente.

    Porteado del GET /info de la superficie per-agente eliminada.
    """
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as ac:
        resp = await ac.get(
            "/admin/agent/info",
            params={"agent_id": "general"},
            headers={"X-Admin-Key": "test-secret"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"id": "general", "name": "Inaki", "description": "Asistente general"}


async def test_agent_info_404_agente_desconocido(admin_app) -> None:
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as ac:
        resp = await ac.get(
            "/admin/agent/info",
            params={"agent_id": "ghost"},
            headers={"X-Admin-Key": "test-secret"},
        )
    assert resp.status_code == 404


async def test_agent_info_401_sin_auth(admin_app) -> None:
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as ac:
        resp = await ac.get("/admin/agent/info", params={"agent_id": "general"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Auth deshabilitada — sin auth_key configurada → 403
# ---------------------------------------------------------------------------


async def test_protected_endpoint_403_when_no_auth_key_configured(admin_app_no_auth) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=admin_app_no_auth), base_url="http://test"
    ) as ac:
        resp = await ac.post("/scheduler/reload")
    assert resp.status_code == 403
