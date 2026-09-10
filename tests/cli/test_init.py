"""``inaki init``: escribe por los use cases de config, valida con el loader y es idempotente."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from inaki.cli import app
from inaki.config.home import set_inaki_home


@pytest.fixture
def home(tmp_path: Path):
    """El callback raíz fija el home con ``--home`` y lo propaga a env: limpiar las dos cosas."""
    h = tmp_path / "inaki-home"
    yield h
    set_inaki_home(None)
    os.environ.pop("INAKI_HOME", None)


def _init(home: Path, entrada: str, *extra: str):
    return CliRunner().invoke(
        app, ["--home", str(home), "init", "--no-model", *extra], input=entrada
    )


# Provider (default) / API key / Base URL / Modelo (default) / Id / Nombre / Descripción /
# System prompt / ¿Telegram? / Token / IDs
_PRIMERA_VEZ = "\nsk-test\n\n\n\nInaki\nAsistente\nSos Inaki.\ny\n123:ABC\n11, 22\n"


def test_primera_vez_escribe_provider_agente_y_telegram_y_valida(home: Path) -> None:
    result = _init(home, _PRIMERA_VEZ)

    assert result.exit_code == 0, result.output
    assert "Configuración válida" in result.output

    global_yaml = yaml.safe_load((home / "config" / "global.yaml").read_text())
    assert global_yaml["providers"]["openrouter"]["api_key"] == "sk-test"
    assert "base_url" not in global_yaml["providers"]["openrouter"]
    assert global_yaml["llm"]["provider"] == "openrouter"
    assert global_yaml["app"]["default_agent"] == "general"

    agente = yaml.safe_load((home / "agents" / "general.yaml").read_text())
    assert agente["name"] == "Inaki" and agente["system_prompt"] == "Sos Inaki."
    assert agente["channels"]["telegram"] == {"token": "123:ABC", "allowed_user_ids": ["11", "22"]}
    assert oct((home / "config" / "global.yaml").stat().st_mode & 0o777) == "0o600"


def test_segunda_vez_no_pisa_la_credencial_ni_crea_otro_agente(home: Path) -> None:
    assert _init(home, _PRIMERA_VEZ).exit_code == 0

    # Provider (default) / ¿Reemplazar api_key? n / Base URL / Modelo / ¿Crear otro agente? n
    result = _init(home, "\nn\n\n\nn\n")

    assert result.exit_code == 0, result.output
    assert "se conserva la credencial existente" in result.output
    global_yaml = yaml.safe_load((home / "config" / "global.yaml").read_text())
    assert global_yaml["providers"]["openrouter"]["api_key"] == "sk-test"
    assert global_yaml["app"]["default_agent"] == "general"
    assert sorted(p.name for p in (home / "agents").glob("*.yaml")) == ["general.yaml"]


def test_provider_desconocido_aborta_antes_de_escribir(home: Path) -> None:
    result = _init(home, "no-existe\n")

    assert result.exit_code == 1
    assert "no es un adapter conocido" in result.output
    assert not list((home / "agents").glob("*.yaml"))
    assert "providers" not in yaml.safe_load((home / "config" / "global.yaml").read_text())


def test_la_descarga_del_modelo_va_al_model_dirname_anclado_al_home(home: Path) -> None:
    with patch("inaki.embedding.download.descargar_modelo_e5") as descargar:
        result = CliRunner().invoke(app, ["--home", str(home), "init"], input=_PRIMERA_VEZ + "y\n")

    assert result.exit_code == 0, result.output
    descargar.assert_called_once()
    assert descargar.call_args.args[0] == home / "models" / "e5-small"
    assert "modelo listo" in result.output
