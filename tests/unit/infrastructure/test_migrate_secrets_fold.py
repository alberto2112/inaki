"""Tests de la migración one-shot que pliega los ``*.secrets.yaml`` en su capa principal.

La migración erradica la capa de secrets: `global.secrets.yaml` → `global.yaml`,
`agents/{id}.secrets.yaml` → `agents/{id}.yaml` (ídem sub-agentes). El contenido
del secrets PISA al de la capa principal — mismo orden de precedencia que tenía
el merge eliminado.
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

import pytest
import yaml

from infrastructure.config_loader import migrate_secrets_into_main_layers

_GLOBAL = """\
# comentario del operador que debe sobrevivir
app:
  default_agent: dev
llm:
  provider: groq
  model: gpt-oss-120b
providers:
  groq:
    base_url: https://api.groq.com/openai/v1
"""

_GLOBAL_SECRETS = """\
providers:
  groq:
    api_key: gsk-secreta
  openai:
    api_key: sk-otra
"""


def _read(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@pytest.fixture
def home(tmp_path: Path) -> tuple[Path, Path]:
    """Devuelve ``(config_dir, agents_dir)`` con el layout real (agents es sibling)."""
    config_dir = tmp_path / "config"
    agents_dir = tmp_path / "agents"
    config_dir.mkdir()
    agents_dir.mkdir()
    return config_dir, agents_dir


def test_pliega_el_global_y_borra_el_secrets(home: tuple[Path, Path]) -> None:
    config_dir, agents_dir = home
    (config_dir / "global.yaml").write_text(_GLOBAL, encoding="utf-8")
    (config_dir / "global.secrets.yaml").write_text(_GLOBAL_SECRETS, encoding="utf-8")

    migrate_secrets_into_main_layers(config_dir, agents_dir)

    doc = _read(config_dir / "global.yaml")
    # lo que ya estaba sigue estando
    assert doc["app"]["default_agent"] == "dev"
    assert doc["providers"]["groq"]["base_url"] == "https://api.groq.com/openai/v1"
    # y las credenciales se plegaron (merge profundo, no reemplazo del bloque)
    assert doc["providers"]["groq"]["api_key"] == "gsk-secreta"
    assert doc["providers"]["openai"]["api_key"] == "sk-otra"

    assert not (config_dir / "global.secrets.yaml").exists()


def test_preserva_los_comentarios_de_la_capa_principal(home: tuple[Path, Path]) -> None:
    config_dir, agents_dir = home
    (config_dir / "global.yaml").write_text(_GLOBAL, encoding="utf-8")
    (config_dir / "global.secrets.yaml").write_text(_GLOBAL_SECRETS, encoding="utf-8")

    migrate_secrets_into_main_layers(config_dir, agents_dir)

    contenido = (config_dir / "global.yaml").read_text(encoding="utf-8")
    assert "# comentario del operador que debe sobrevivir" in contenido


def test_el_secrets_pisa_a_la_capa_principal(home: tuple[Path, Path]) -> None:
    """Mismo orden de precedencia que el merge que se elimina: secrets gana."""
    config_dir, agents_dir = home
    (config_dir / "global.yaml").write_text("llm:\n  model: viejo\n", encoding="utf-8")
    (config_dir / "global.secrets.yaml").write_text("llm:\n  model: nuevo\n", encoding="utf-8")

    migrate_secrets_into_main_layers(config_dir, agents_dir)

    assert _read(config_dir / "global.yaml")["llm"]["model"] == "nuevo"


def test_la_capa_principal_queda_0600(home: tuple[Path, Path]) -> None:
    config_dir, agents_dir = home
    global_yaml = config_dir / "global.yaml"
    global_yaml.write_text(_GLOBAL, encoding="utf-8")
    global_yaml.chmod(0o644)  # permisos laxos previos a la migración
    (config_dir / "global.secrets.yaml").write_text(_GLOBAL_SECRETS, encoding="utf-8")

    migrate_secrets_into_main_layers(config_dir, agents_dir)

    assert stat.S_IMODE(global_yaml.stat().st_mode) == 0o600


def test_pliega_agentes_y_subagentes(home: tuple[Path, Path]) -> None:
    config_dir, agents_dir = home
    sub_dir = agents_dir / "sub-agents"
    sub_dir.mkdir()

    (agents_dir / "dev.yaml").write_text("id: dev\nname: Dev\n", encoding="utf-8")
    (agents_dir / "dev.secrets.yaml").write_text(
        "channels:\n  telegram:\n    token: tok-dev\n", encoding="utf-8"
    )
    (sub_dir / "researcher.yaml").write_text("id: researcher\nname: R\n", encoding="utf-8")
    (sub_dir / "researcher.secrets.yaml").write_text(
        "providers:\n  groq:\n    api_key: sk-sub\n", encoding="utf-8"
    )

    migrate_secrets_into_main_layers(config_dir, agents_dir)

    dev = _read(agents_dir / "dev.yaml")
    assert dev["id"] == "dev"
    assert dev["channels"]["telegram"]["token"] == "tok-dev"
    assert not (agents_dir / "dev.secrets.yaml").exists()

    sub = _read(sub_dir / "researcher.yaml")
    assert sub["providers"]["groq"]["api_key"] == "sk-sub"
    assert not (sub_dir / "researcher.secrets.yaml").exists()


def test_idempotente_sin_secrets_no_toca_nada(home: tuple[Path, Path]) -> None:
    config_dir, agents_dir = home
    global_yaml = config_dir / "global.yaml"
    global_yaml.write_text(_GLOBAL, encoding="utf-8")
    antes = global_yaml.read_text(encoding="utf-8")

    migrate_secrets_into_main_layers(config_dir, agents_dir)
    migrate_secrets_into_main_layers(config_dir, agents_dir)

    assert global_yaml.read_text(encoding="utf-8") == antes


def test_secrets_vacio_se_borra_sin_reescribir_la_principal(home: tuple[Path, Path]) -> None:
    """Un secrets con solo comentarios no aporta datos: se borra y la principal queda intacta."""
    config_dir, agents_dir = home
    global_yaml = config_dir / "global.yaml"
    global_yaml.write_text(_GLOBAL, encoding="utf-8")
    antes = global_yaml.read_text(encoding="utf-8")
    (config_dir / "global.secrets.yaml").write_text("# solo un header\n", encoding="utf-8")

    migrate_secrets_into_main_layers(config_dir, agents_dir)

    assert not (config_dir / "global.secrets.yaml").exists()
    assert global_yaml.read_text(encoding="utf-8") == antes


def test_secrets_sin_capa_principal_crea_el_fichero(home: tuple[Path, Path]) -> None:
    """Un secrets huérfano no se pierde: su contenido funda la capa principal."""
    config_dir, agents_dir = home
    (config_dir / "global.secrets.yaml").write_text(_GLOBAL_SECRETS, encoding="utf-8")

    migrate_secrets_into_main_layers(config_dir, agents_dir)

    doc = _read(config_dir / "global.yaml")
    assert doc["providers"]["groq"]["api_key"] == "gsk-secreta"
    assert not (config_dir / "global.secrets.yaml").exists()


def test_descarta_tool_config_ya_migrado(home: tuple[Path, Path]) -> None:
    """Si `tool_config.yaml` ya existe, el bloque residual del secrets no ensucia el global."""
    config_dir, agents_dir = home
    (config_dir / "global.yaml").write_text(_GLOBAL, encoding="utf-8")
    (config_dir / "tool_config.yaml").write_text("tool_config:\n  exchange:\n    a: 1\n")
    (config_dir / "global.secrets.yaml").write_text(
        "tool_config:\n  exchange:\n    a: 1\nproviders:\n  groq:\n    api_key: gsk\n",
        encoding="utf-8",
    )

    migrate_secrets_into_main_layers(config_dir, agents_dir)

    doc = _read(config_dir / "global.yaml")
    assert "tool_config" not in doc
    assert doc["providers"]["groq"]["api_key"] == "gsk"


def test_conserva_tool_config_si_no_se_migro(home: tuple[Path, Path]) -> None:
    """Sin `tool_config.yaml`, el bloque es dato del usuario: se pliega en vez de perderse."""
    config_dir, agents_dir = home
    (config_dir / "global.yaml").write_text(_GLOBAL, encoding="utf-8")
    (config_dir / "global.secrets.yaml").write_text(
        "tool_config:\n  exchange:\n    username: alberto\n", encoding="utf-8"
    )

    migrate_secrets_into_main_layers(config_dir, agents_dir)

    doc = _read(config_dir / "global.yaml")
    assert doc["tool_config"]["exchange"]["username"] == "alberto"


def test_no_borra_el_secrets_si_falla_la_escritura(
    home: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regla del repo: escribir lo nuevo ANTES de borrar lo viejo. Si falla, nada se pierde."""
    config_dir, agents_dir = home
    global_yaml = config_dir / "global.yaml"
    global_yaml.write_text(_GLOBAL, encoding="utf-8")
    secrets = config_dir / "global.secrets.yaml"
    secrets.write_text(_GLOBAL_SECRETS, encoding="utf-8")

    open_real = Path.open

    def open_falla(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == global_yaml and args and args[0] == "w":
            raise OSError("disco lleno")
        return open_real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_falla)

    migrate_secrets_into_main_layers(config_dir, agents_dir)

    assert secrets.exists()
    assert _read(secrets)["providers"]["groq"]["api_key"] == "gsk-secreta"
