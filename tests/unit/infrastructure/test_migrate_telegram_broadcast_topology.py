"""Tests de la migración one-shot que reestructura ``channels.telegram.broadcast``
del formato implícito (``port``/``remote``) al de rol explícito (``server``/``client``
+ ``auth`` único)."""

from __future__ import annotations

from pathlib import Path

import yaml

from infrastructure.config_loader import migrate_telegram_broadcast_topology

_GLOBAL_SERVER = """\
channels:
  telegram:
    token: "TOKEN"  # comentario del operador
    broadcast:
      port: 6499
      auth: "shared-secret"
      emit:
        user_input_voice: true
"""

_GLOBAL_CLIENT = """\
channels:
  telegram:
    broadcast:
      remote:
        host: "192.168.1.50:6499"
        auth: "shared-secret"
"""


def _tg(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["channels"]["telegram"]


def test_server_port_pasa_a_bloque_server(tmp_path: Path):
    (tmp_path / "global.yaml").write_text(_GLOBAL_SERVER, encoding="utf-8")

    migrate_telegram_broadcast_topology(tmp_path)

    bc = _tg(tmp_path / "global.yaml")["broadcast"]
    assert bc["server"] == {"port": 6499}
    assert bc["auth"] == "shared-secret"
    assert "port" not in bc
    # emit no se toca.
    assert bc["emit"] == {"user_input_voice": True}


def test_client_remote_pasa_a_bloque_client_con_auth_unificado(tmp_path: Path):
    (tmp_path / "global.yaml").write_text(_GLOBAL_CLIENT, encoding="utf-8")

    migrate_telegram_broadcast_topology(tmp_path)

    bc = _tg(tmp_path / "global.yaml")["broadcast"]
    assert bc["client"] == {"host": "192.168.1.50", "port": 6499}
    assert bc["auth"] == "shared-secret"
    assert "remote" not in bc


def test_remote_solo_auth_capa_secrets(tmp_path: Path):
    """Caso real del merge de 4 capas: el secrets solo tiene ``remote.auth``
    (el host vive en la capa principal). El auth sube al nivel broadcast."""
    (tmp_path / "global.secrets.yaml").write_text(
        "channels:\n  telegram:\n    broadcast:\n      remote:\n        auth: s3cret\n",
        encoding="utf-8",
    )

    migrate_telegram_broadcast_topology(tmp_path)

    bc = _tg(tmp_path / "global.secrets.yaml")["broadcast"]
    assert bc == {"auth": "s3cret"}


def test_auth_existente_gana_ante_remote_auth(tmp_path: Path):
    (tmp_path / "global.yaml").write_text(
        "channels:\n"
        "  telegram:\n"
        "    broadcast:\n"
        "      auth: gana\n"
        "      remote:\n"
        "        host: 'h:1024'\n"
        "        auth: pierde\n",
        encoding="utf-8",
    )

    migrate_telegram_broadcast_topology(tmp_path)

    bc = _tg(tmp_path / "global.yaml")["broadcast"]
    assert bc["auth"] == "gana"
    assert bc["client"] == {"host": "h", "port": 1024}


def test_host_sin_puerto_migra_solo_host(tmp_path: Path):
    """remote.host sin ':puerto' parseable: se migra el host crudo y el schema
    reclamará client.port al cargar (mejor un error visible que un skip mudo)."""
    (tmp_path / "global.yaml").write_text(
        "channels:\n"
        "  telegram:\n"
        "    broadcast:\n"
        "      remote:\n"
        "        host: 'solo-host'\n"
        "        auth: s\n",
        encoding="utf-8",
    )

    migrate_telegram_broadcast_topology(tmp_path)

    bc = _tg(tmp_path / "global.yaml")["broadcast"]
    assert bc["client"] == {"host": "solo-host"}
    assert bc["auth"] == "s"


def test_procesa_agents_yaml(tmp_path: Path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "dev.yaml").write_text(_GLOBAL_CLIENT, encoding="utf-8")

    migrate_telegram_broadcast_topology(tmp_path)

    bc = _tg(agents / "dev.yaml")["broadcast"]
    assert bc["client"] == {"host": "192.168.1.50", "port": 6499}


def test_idempotente_y_preserva_comentarios(tmp_path: Path):
    p = tmp_path / "global.yaml"
    p.write_text(_GLOBAL_SERVER, encoding="utf-8")

    migrate_telegram_broadcast_topology(tmp_path)
    primera = p.read_text(encoding="utf-8")
    migrate_telegram_broadcast_topology(tmp_path)
    segunda = p.read_text(encoding="utf-8")

    assert primera == segunda  # segunda corrida no toca nada
    assert "# comentario del operador" in segunda  # ruamel preservó el comentario


def test_formato_nuevo_no_se_toca(tmp_path: Path):
    p = tmp_path / "global.yaml"
    original = (
        "channels:\n"
        "  telegram:\n"
        "    broadcast:\n"
        "      enabled: true\n"
        "      auth: s\n"
        "      server:\n"
        "        port: 6499\n"
    )
    p.write_text(original, encoding="utf-8")

    migrate_telegram_broadcast_topology(tmp_path)

    assert p.read_text(encoding="utf-8") == original


def test_noop_sin_broadcast(tmp_path: Path):
    p = tmp_path / "global.yaml"
    original = "channels:\n  telegram:\n    groups:\n      behavior: mention\n"
    p.write_text(original, encoding="utf-8")

    migrate_telegram_broadcast_topology(tmp_path)

    assert p.read_text(encoding="utf-8") == original


def test_noop_si_no_existen_archivos(tmp_path: Path):
    migrate_telegram_broadcast_topology(tmp_path)  # no debe explotar
