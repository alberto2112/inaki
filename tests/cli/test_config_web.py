"""``inaki config web``: loopback sin auth; fuera de loopback, ``admin.auth_key`` obligatoria."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

from inaki.channels import registrar_canales_instalados
from inaki.cli import app
from inaki.cli.config_web import construir_app, es_loopback
from inaki.config.home import set_inaki_home


@pytest.fixture
def home(tmp_path: Path) -> Iterator[Path]:
    registrar_canales_instalados()
    h = tmp_path / "home"
    (h / "config").mkdir(parents=True)
    (h / "agents").mkdir()
    (h / "config" / "global.yaml").write_text("app:\n  default_agent: dev\n")
    (h / "agents" / "dev.yaml").write_text("id: dev\nname: Dev\ndescription: d\n")
    set_inaki_home(h)
    yield h
    set_inaki_home(None)
    os.environ.pop("INAKI_HOME", None)


def test_loopback_es_solo_localhost() -> None:
    assert es_loopback("127.0.0.1") and es_loopback("localhost") and es_loopback("::1")
    assert not es_loopback("0.0.0.0") and not es_loopback("192.168.1.10")


async def test_en_loopback_no_pide_key(home: Path) -> None:
    web = construir_app(host="127.0.0.1", auth_key=None)
    async with AsyncClient(transport=ASGITransport(app=web), base_url="http://t") as ac:
        r = await ac.get("/admin/config/agents")
    assert r.status_code == 200 and r.json()["agents"] == ["dev"]


async def test_fuera_de_loopback_exige_la_key_del_admin(home: Path) -> None:
    web = construir_app(host="0.0.0.0", auth_key="k-lan")
    async with AsyncClient(transport=ASGITransport(app=web), base_url="http://t") as ac:
        sin = await ac.get("/admin/config/agents")
        con = await ac.get("/admin/config/agents", headers={"X-Admin-Key": "k-lan"})
    assert sin.status_code == 401
    assert con.status_code == 200


def test_fuera_de_loopback_sin_key_configurada_no_arranca(home: Path) -> None:
    with pytest.raises(ValueError, match="admin.auth_key"):
        construir_app(host="0.0.0.0", auth_key=None)

    with patch("uvicorn.run") as run:
        result = CliRunner().invoke(
            app, ["--home", str(home), "config", "web", "--host", "0.0.0.0"]
        )
    assert result.exit_code == 1, result.output
    assert "admin.auth_key" in result.output
    run.assert_not_called()


def test_fuera_de_loopback_con_key_en_global_arranca(home: Path) -> None:
    (home / "config" / "global.yaml").write_text(
        "app:\n  default_agent: dev\nadmin:\n  auth_key: k-lan\n"
    )
    with patch("uvicorn.run") as run:
        result = CliRunner().invoke(
            app, ["--home", str(home), "config", "web", "--host", "0.0.0.0", "--port", "7000"]
        )
    assert result.exit_code == 0, result.output
    assert "con X-Admin-Key" in result.output
    run.assert_called_once()
    assert run.call_args.kwargs["host"] == "0.0.0.0" and run.call_args.kwargs["port"] == 7000
    assert run.call_args.args[0].state.admin_auth_key == "k-lan"
