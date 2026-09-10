"""``/admin/config``: vista efectiva con origen y schema, edición validada, los dos modos."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from inaki.channels import registrar_canales_instalados
from inaki.channels.rest.app import create_admin_app
from inaki.channels.rest.routers.config import router
from inaki.channels.rest.routers.deps import check_admin_auth
from inaki.config.home import set_inaki_home
from inaki.config.wiring import build_config_web

KEY = "clave-test"


@pytest.fixture
def home(tmp_path: Path) -> Iterator[Path]:
    """Un home con global + un agente con Telegram y un sub-agente, que el loader carga."""
    registrar_canales_instalados()
    h = tmp_path / "home"
    (h / "config").mkdir(parents=True)
    (h / "agents" / "sub-agents").mkdir(parents=True)
    (h / "config" / "global.yaml").write_text(
        "app:\n  default_agent: dev\n"
        "providers:\n  openrouter:\n    api_key: sk-real\n"
        "llm:\n  provider: openrouter\n  model: base\n"
        "admin:\n  auth_key: k\n"
    )
    (h / "agents" / "dev.yaml").write_text(
        "id: dev\nname: Dev\ndescription: Dev\nllm:\n  model: override\n"
        "channels:\n  telegram:\n    token: '123:abc'\n"
    )
    (h / "agents" / "sub-agents" / "worker.yaml").write_text(
        "id: worker\nname: Worker\ndescription: W\n"
    )
    set_inaki_home(h)
    yield h
    set_inaki_home(None)


@pytest.fixture
def daemon_app(home: Path) -> FastAPI:
    return create_admin_app(
        MagicMock(), admin_auth_key=KEY, config_web=build_config_web(daemon=True)
    )


@pytest.fixture
def standalone_app(home: Path) -> FastAPI:
    app = FastAPI()
    app.state.config_web = build_config_web(daemon=False)
    app.dependency_overrides[check_admin_auth] = lambda: None
    app.include_router(router)
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _campo(d: dict, path: str) -> dict:
    return next(c for c in d["campos"] if c["path"] == path)


async def test_en_el_daemon_exige_la_key_y_lista_agentes(daemon_app: FastAPI) -> None:
    async with _client(daemon_app) as ac:
        assert (await ac.get("/admin/config/agents")).status_code == 401
        r = await ac.get("/admin/config/agents", headers={"X-Admin-Key": KEY})
    assert r.status_code == 200
    assert r.json() == {"agents": ["dev"], "sub_agents": ["worker"], "daemon": True}


async def test_la_vista_trae_origen_schema_y_secretos_redactados(daemon_app: FastAPI) -> None:
    async with _client(daemon_app) as ac:
        r = await ac.get("/admin/config/effective?agent=dev", headers={"X-Admin-Key": KEY})
    assert r.status_code == 200
    d = r.json()
    modelo = _campo(d, "llm.model")
    assert modelo["valor"] == "override" and modelo["origen"] == "agent"
    assert modelo["tipo"] == "str" and "modelo" in modelo["doc"].lower()
    assert _campo(d, "llm.provider")["origen"] == "global"
    assert _campo(d, "llm.temperature")["origen"] == "default"
    token = _campo(d, "channels.telegram.token")
    assert token["secreto"] and token["valor"] == "********" and token["configurado"]
    assert token["doc"].startswith("Token del bot"), "la ayuda del canal llega por comodín"
    assert _campo(d, "providers.openrouter.api_key")["valor"] == "********"
    assert "sk-real" not in r.text and "123:abc" not in r.text


async def test_put_valido_escribe_valida_y_devuelve_la_vista_nueva(
    daemon_app: FastAPI, home: Path
) -> None:
    body = {
        "layer": "agent",
        "agent_id": "dev",
        "cambios": {"llm.temperature": 0.1},
        "heredar": ["llm.model"],
    }
    async with _client(daemon_app) as ac:
        r = await ac.put("/admin/config/layer", json=body, headers={"X-Admin-Key": KEY})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["cambiados"] == ["llm.temperature"] and d["heredados"] == ["llm.model"]
    assert (
        _campo(d, "llm.model")["valor"] == "base" and _campo(d, "llm.model")["origen"] == "global"
    )
    assert _campo(d, "llm.temperature")["origen"] == "agent"
    assert yaml.safe_load((home / "agents" / "dev.yaml").read_text())["llm"] == {"temperature": 0.1}


async def test_put_que_el_loader_rechaza_devuelve_422_y_no_toca_el_disco(
    standalone_app: FastAPI, home: Path
) -> None:
    antes = (home / "config" / "global.yaml").read_text()
    async with _client(standalone_app) as ac:
        r = await ac.put(
            "/admin/config/layer", json={"layer": "global", "cambios": {"llm.modle": "x"}}
        )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error"] == "config_invalida"
    assert "modle" in r.json()["detail"]["mensaje"]
    assert (home / "config" / "global.yaml").read_text() == antes


async def test_standalone_no_pide_key_y_sirve_la_ui(standalone_app: FastAPI) -> None:
    async with _client(standalone_app) as ac:
        agentes = await ac.get("/admin/config/agents")
        ui = await ac.get("/admin/config/ui")
    assert agentes.status_code == 200 and agentes.json()["daemon"] is False
    assert ui.status_code == 200 and "Inaki · Config" in ui.text
    assert "text/html" in ui.headers["content-type"]


async def test_sin_config_web_montada_responde_503() -> None:
    app = create_admin_app(MagicMock(), admin_auth_key=KEY)
    async with _client(app) as ac:
        r = await ac.get("/admin/config/agents", headers={"X-Admin-Key": KEY})
    assert r.status_code == 503
