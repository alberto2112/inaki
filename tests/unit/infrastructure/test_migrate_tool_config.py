"""Tests de la migración one-shot del bloque tool_config a su propio archivo."""

from __future__ import annotations

import stat
from pathlib import Path

import yaml

from infrastructure.config_loader import migrate_tool_config_to_own_file

_SECRETS_CON_TOOL_CONFIG = """\
# credenciales del operador
providers:
  openai:
    api_key: sk-123
tool_config:
  exchange:
    username: alberto
    password: "enc:gAAAAblob"
  web_search:
    max_results: 5
"""


def _read(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def test_mueve_el_bloque_y_limpia_el_secrets(tmp_path: Path):
    secrets = tmp_path / "global.secrets.yaml"
    secrets.write_text(_SECRETS_CON_TOOL_CONFIG, encoding="utf-8")

    migrate_tool_config_to_own_file(tmp_path)

    store_path = tmp_path / "tool_config.yaml"
    # el bloque vive ahora en el archivo propio, bajo raíz tool_config
    store_doc = _read(store_path)
    assert store_doc["tool_config"]["exchange"]["username"] == "alberto"
    assert store_doc["tool_config"]["exchange"]["password"] == "enc:gAAAAblob"  # verbatim
    assert store_doc["tool_config"]["web_search"]["max_results"] == 5

    # global.secrets.yaml perdió el bloque pero conservó las credenciales del operador
    secrets_doc = _read(secrets)
    assert "tool_config" not in secrets_doc
    assert secrets_doc["providers"]["openai"]["api_key"] == "sk-123"


def test_archivo_nuevo_queda_0600(tmp_path: Path):
    (tmp_path / "global.secrets.yaml").write_text(_SECRETS_CON_TOOL_CONFIG, encoding="utf-8")

    migrate_tool_config_to_own_file(tmp_path)

    store_path = tmp_path / "tool_config.yaml"
    assert stat.S_IMODE(store_path.stat().st_mode) == 0o600


def test_idempotente_no_pisa_si_ya_existe_el_archivo_propio(tmp_path: Path):
    """Si tool_config.yaml ya existe, la migración no toca nada (segunda corrida)."""
    secrets = tmp_path / "global.secrets.yaml"
    secrets.write_text(_SECRETS_CON_TOOL_CONFIG, encoding="utf-8")
    store_path = tmp_path / "tool_config.yaml"
    store_path.write_text("tool_config:\n  ya_migrado:\n    flag: true\n", encoding="utf-8")

    migrate_tool_config_to_own_file(tmp_path)

    # no se pisó el archivo propio existente...
    assert _read(store_path)["tool_config"] == {"ya_migrado": {"flag": True}}
    # ...y el secrets queda como estaba (no se re-limpia)
    assert "tool_config" in _read(secrets)


def test_noop_si_secrets_no_tiene_bloque(tmp_path: Path):
    secrets = tmp_path / "global.secrets.yaml"
    secrets.write_text("providers:\n  openai:\n    api_key: sk-123\n", encoding="utf-8")

    migrate_tool_config_to_own_file(tmp_path)

    assert not (tmp_path / "tool_config.yaml").exists()
    assert _read(secrets)["providers"]["openai"]["api_key"] == "sk-123"


def test_noop_si_no_existe_secrets(tmp_path: Path):
    migrate_tool_config_to_own_file(tmp_path)  # no debe explotar
    assert not (tmp_path / "tool_config.yaml").exists()
