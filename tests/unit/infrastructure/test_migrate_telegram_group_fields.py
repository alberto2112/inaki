"""Tests de la migración one-shot que mueve la política de respuesta en grupos
de ``channels.telegram.broadcast`` a ``channels.telegram.groups``."""

from __future__ import annotations

from pathlib import Path

import yaml

from infrastructure.config_loader import migrate_telegram_group_fields

# Caso típico: cliente broadcast (remote+auth) con política de respuesta mezclada.
_GLOBAL_CON_MIX = """\
channels:
  telegram:
    token: "TOKEN"  # comentario del operador
    groups:
      min_delay_response: 9.5
      reactions: true
    broadcast:
      behavior: autonomous
      bot_username: "anacleto_ia_bot"
      rate_limiter: 1
      rate_limiter_window: 65
      remote:
        host: "192.168.1.50:6499"
        auth: "shared-secret"
"""


def _tg(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["channels"]["telegram"]


def test_mueve_politica_a_groups_y_conserva_transporte(tmp_path: Path):
    (tmp_path / "global.yaml").write_text(_GLOBAL_CON_MIX, encoding="utf-8")

    migrate_telegram_group_fields(tmp_path, tmp_path / "agents")

    tg = _tg(tmp_path / "global.yaml")
    # Los 4 campos de comportamiento aterrizaron en groups (junto a los que ya estaban).
    assert tg["groups"]["behavior"] == "autonomous"
    assert tg["groups"]["bot_username"] == "anacleto_ia_bot"
    assert tg["groups"]["rate_limiter"] == 1
    assert tg["groups"]["rate_limiter_window"] == 65
    assert tg["groups"]["min_delay_response"] == 9.5
    assert tg["groups"]["reactions"] is True
    # El transporte TCP queda intacto en broadcast; sin restos de comportamiento.
    assert tg["broadcast"] == {"remote": {"host": "192.168.1.50:6499", "auth": "shared-secret"}}


def test_crea_groups_si_no_existe(tmp_path: Path):
    (tmp_path / "global.yaml").write_text(
        "channels:\n"
        "  telegram:\n"
        "    broadcast:\n"
        "      port: 6499\n"
        "      auth: secret\n"
        "      behavior: mention\n"
        "      bot_username: bot\n",
        encoding="utf-8",
    )

    migrate_telegram_group_fields(tmp_path, tmp_path / "agents")

    tg = _tg(tmp_path / "global.yaml")
    assert tg["groups"] == {"behavior": "mention", "bot_username": "bot"}
    assert tg["broadcast"] == {"port": 6499, "auth": "secret"}


def test_broadcast_sin_transporte_se_elimina(tmp_path: Path):
    """Un broadcast que solo tenía comportamiento queda vacío → se borra el bloque
    (no debe disparar el validador port-XOR-remote)."""
    (tmp_path / "global.yaml").write_text(
        "channels:\n  telegram:\n    broadcast:\n      behavior: mention\n      bot_username: bot\n",
        encoding="utf-8",
    )

    migrate_telegram_group_fields(tmp_path, tmp_path / "agents")

    tg = _tg(tmp_path / "global.yaml")
    assert "broadcast" not in tg
    assert tg["groups"] == {"behavior": "mention", "bot_username": "bot"}


def test_groups_gana_ante_conflicto(tmp_path: Path):
    (tmp_path / "global.yaml").write_text(
        "channels:\n"
        "  telegram:\n"
        "    groups:\n"
        "      behavior: listen\n"
        "    broadcast:\n"
        "      port: 6499\n"
        "      auth: secret\n"
        "      behavior: autonomous\n",
        encoding="utf-8",
    )

    migrate_telegram_group_fields(tmp_path, tmp_path / "agents")

    tg = _tg(tmp_path / "global.yaml")
    # El valor que ya estaba en groups gana; el de broadcast se descarta.
    assert tg["groups"]["behavior"] == "listen"
    assert "behavior" not in tg["broadcast"]


def test_procesa_agents_yaml(tmp_path: Path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "dev.yaml").write_text(
        "channels:\n"
        "  telegram:\n"
        "    broadcast:\n"
        "      remote:\n"
        "        host: 'h:1'\n"
        "        auth: s\n"
        "      behavior: autonomous\n",
        encoding="utf-8",
    )

    migrate_telegram_group_fields(tmp_path, tmp_path / "agents")

    tg = _tg(agents / "dev.yaml")
    assert tg["groups"] == {"behavior": "autonomous"}
    assert tg["broadcast"] == {"remote": {"host": "h:1", "auth": "s"}}


def test_idempotente_y_preserva_comentarios(tmp_path: Path):
    p = tmp_path / "global.yaml"
    p.write_text(_GLOBAL_CON_MIX, encoding="utf-8")

    migrate_telegram_group_fields(tmp_path, tmp_path / "agents")
    primera = p.read_text(encoding="utf-8")
    migrate_telegram_group_fields(tmp_path, tmp_path / "agents")
    segunda = p.read_text(encoding="utf-8")

    assert primera == segunda  # segunda corrida no toca nada
    assert "# comentario del operador" in segunda  # ruamel preservó el comentario


def test_noop_sin_broadcast(tmp_path: Path):
    p = tmp_path / "global.yaml"
    original = "channels:\n  telegram:\n    groups:\n      behavior: mention\n"
    p.write_text(original, encoding="utf-8")

    migrate_telegram_group_fields(tmp_path, tmp_path / "agents")

    assert p.read_text(encoding="utf-8") == original


def test_noop_si_no_existen_archivos(tmp_path: Path):
    migrate_telegram_group_fields(tmp_path, tmp_path / "agents")  # no debe explotar


def test_layout_real_agents_sibling_de_config(tmp_path):
    """Regresión del bug de path: en el layout REAL (``~/.inaki/agents/`` sibling
    de ``config/``) la migración derivaba agents como ``config_dir/"agents"`` y
    NUNCA tocaba los ficheros de agente. Con los campos viejos ignorados en
    silencio nadie lo notó; desde `config-falla-ruidoso` el agente sin migrar
    aborta el arranque."""
    config_dir = tmp_path / "config"
    agents_dir = tmp_path / "agents"
    config_dir.mkdir()
    (agents_dir / "sub-agents").mkdir(parents=True)
    (agents_dir / "dev.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "dev",
                "channels": {
                    "telegram": {"broadcast": {"behavior": "autonomous", "rate_limiter": 3}}
                },
            }
        ),
        encoding="utf-8",
    )

    migrate_telegram_group_fields(config_dir, agents_dir)

    doc = yaml.safe_load((agents_dir / "dev.yaml").read_text(encoding="utf-8"))
    telegram = doc["channels"]["telegram"]
    assert telegram["groups"] == {"behavior": "autonomous", "rate_limiter": 3}
    assert "broadcast" not in telegram, "el bloque vacío debe eliminarse (guard port-XOR-remote)"
