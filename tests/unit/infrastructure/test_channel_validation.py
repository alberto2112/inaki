"""Guard de la validación de ``channels`` contra ``CHANNEL_SCHEMAS``.

Antes ``AgentConfig.channels`` era un ``dict[str, dict[str, Any]]`` opaco: sus
26 campos —el 14% del schema— no se validaban NUNCA al cargar. Un typo de tipo,
una topología de broadcast inválida o un canal inexistente viajaban hasta el
primer uso en runtime, o se comían un default silencioso.

La validación vive en el schema (``field_validator`` de ``AgentConfig``) y no en
el loader a propósito: es la ÚNICA puerta, y cubre por igual los cuatro caminos
que construyen un ``AgentConfig`` (loader, builder efímero del delegate, admin
server y tests).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from core.domain.errors import ConfigError

from infrastructure.config import (
    CHANNEL_SCHEMAS,
    AgentConfig,
    CliChannelConfig,
    TelegramChannelConfig,
    load_agent_config,
    load_global_config,
)

_BASE: dict[str, Any] = {
    "id": "dev",
    "name": "Dev",
    "description": "agente de prueba",
    "llm": {},
    "embedding": {},
    "memories": {},
    "chat_history": {},
}


def _agente(channels: dict) -> AgentConfig:
    return AgentConfig(**_BASE, channels=channels)


# ---------------------------------------------------------------------------
# Coerción a modelo tipado
# ---------------------------------------------------------------------------


def test_el_bloque_llega_tipado_y_con_los_defaults_del_schema() -> None:
    cfg = _agente({"telegram": {"token": "TKN"}})

    assert isinstance(cfg.telegram, TelegramChannelConfig)
    assert cfg.telegram.token == "TKN"
    # Los defaults los pone el schema — no cada consumidor por su cuenta.
    assert cfg.telegram.voice_enabled is True
    assert cfg.telegram.reactions is False


def test_property_devuelve_none_si_el_canal_no_esta_declarado() -> None:
    cfg = _agente({"cli": {"user": "alberto"}})

    assert cfg.telegram is None
    assert isinstance(cfg.cli, CliChannelConfig)
    assert cfg.cli.user == "alberto"


def test_bloque_vacio_o_nulo_equivale_a_todos_los_defaults() -> None:
    assert _agente({"telegram": {}}).telegram.token == ""  # type: ignore[union-attr]
    assert _agente({"telegram": None}).telegram.token == ""  # type: ignore[union-attr]


def test_un_modelo_ya_construido_pasa_sin_revalidar() -> None:
    """El builder efímero y el admin pueden pasar el modelo directo."""
    modelo = TelegramChannelConfig(token="T")

    assert _agente({"telegram": modelo}).telegram is modelo


def test_sin_channels_no_hay_canales() -> None:
    cfg = AgentConfig(**_BASE)

    assert cfg.channels == {}
    assert cfg.telegram is None and cfg.cli is None


# ---------------------------------------------------------------------------
# Lo que ahora FALLA y antes pasaba en silencio
# ---------------------------------------------------------------------------


def test_canal_desconocido_es_error_y_lista_los_soportados() -> None:
    with pytest.raises(ValidationError) as exc:
        _agente({"slack": {"token": "x"}})

    mensaje = str(exc.value)
    assert "channels.slack" in mensaje
    assert "telegram" in mensaje and "cli" in mensaje


def test_bloque_broadcast_en_el_nivel_equivocado_aborta() -> None:
    """``channels.broadcast`` no es un canal: el único path válido es
    ``channels.telegram.broadcast``. Antes se descartaba y el puerto quedaba
    cerrado sin una pista (nota `broadcast-arranque-observable`)."""
    with pytest.raises(ValidationError, match="channels.broadcast"):
        _agente({"broadcast": {"server": {"port": 6499}}})


def test_tipo_invalido_en_un_campo_del_canal() -> None:
    with pytest.raises(ValidationError, match="channels.telegram"):
        _agente({"telegram": {"reactions": "puede ser"}})


def test_bloque_que_no_es_un_mapa() -> None:
    with pytest.raises(ValidationError, match="channels.telegram"):
        _agente({"telegram": "token-suelto"})


@pytest.mark.parametrize(
    "broadcast, motivo",
    [
        ({"enabled": True, "port": 6499, "auth": "s"}, "formato viejo: port suelto"),
        ({"enabled": True, "remote": "otro:6499", "auth": "s"}, "formato viejo: remote"),
        ({"enabled": True, "server": {"port": 6499}}, "sin auth"),
        ({"enabled": True, "server": {"port": 80}, "auth": "s"}, "puerto privilegiado"),
        (
            {
                "enabled": True,
                "server": {"port": 6499},
                "client": {"host": "h", "port": 6499},
                "auth": "s",
            },
            "server XOR client",
        ),
    ],
)
def test_topologia_de_broadcast_invalida_aborta(broadcast: dict, motivo: str) -> None:
    """El wiring ya no puede tragarse esto en silencio: falla al construir.

    Antes ``_wire_broadcast_for_agent`` revalidaba el bloque en un try/except y,
    al fallar, se llevaba puestos el transporte de broadcast Y el rate limiter de
    grupos con el daemon arrancando sano.
    """
    with pytest.raises(ValidationError, match="channels.telegram"):
        _agente({"telegram": {"token": "T", "broadcast": broadcast}})


# ---------------------------------------------------------------------------
# El registry es la fuente única
# ---------------------------------------------------------------------------


def test_el_registry_cubre_los_canales_que_el_schema_expone() -> None:
    """Si alguien agrega un canal, tiene que estar en el registry: es lo que
    validan el loader, el setup TUI y el generador de la referencia."""
    assert set(CHANNEL_SCHEMAS) == {"telegram", "cli"}
    assert CHANNEL_SCHEMAS["telegram"] is TelegramChannelConfig
    assert CHANNEL_SCHEMAS["cli"] is CliChannelConfig


# ---------------------------------------------------------------------------
# Integración con el loader
# ---------------------------------------------------------------------------


def _escribir_home(tmp_path: Path, agente_yaml: str) -> tuple[Path, Path]:
    cfg_dir, agents_dir = tmp_path / "config", tmp_path / "agents"
    cfg_dir.mkdir()
    agents_dir.mkdir()
    (cfg_dir / "global.yaml").write_text(
        "app: {name: Test}\n"
        "llm: {provider: groq, model: m}\n"
        "embedding: {provider: e5_onnx, model_dirname: /tmp/m}\n"
        "memories: {db_filename: ':memory:'}\n"
        "chat_history: {db_filename: /tmp/h.db}\n",
        encoding="utf-8",
    )
    (agents_dir / "dev.yaml").write_text(agente_yaml, encoding="utf-8")
    return cfg_dir, agents_dir


def test_el_loader_entrega_el_canal_tipado(tmp_path: Path) -> None:
    cfg_dir, agents_dir = _escribir_home(
        tmp_path,
        "id: dev\nname: Dev\ndescription: d\n"
        "channels:\n  telegram:\n    token: TKN\n    allowed_chat_ids: [-100]\n",
    )
    _, global_raw = load_global_config(cfg_dir)

    agente = load_agent_config("dev", agents_dir, global_raw)

    assert agente is not None
    assert isinstance(agente.telegram, TelegramChannelConfig)
    assert agente.telegram.allowed_chat_ids == [-100]


def test_el_loader_aborta_con_un_canal_invalido(tmp_path: Path) -> None:
    """El canal roto ABORTA el arranque nombrando su path y el fichero.

    La Fase 2 hizo que el error se detectara (antes ni siquiera se validaba);
    la Fase 4 lo convirtió en fatal. Hasta entonces el agente desaparecía del
    registry con un WARNING, que en la práctica significa un bot que no
    responde sin nada que lo relacione con el daemon "sano".
    """
    cfg_dir, agents_dir = _escribir_home(
        tmp_path,
        "id: dev\nname: Dev\ndescription: d\nchannels:\n  slack:\n    token: TKN\n",
    )
    _, global_raw = load_global_config(cfg_dir)

    with pytest.raises(ConfigError) as exc:
        load_agent_config("dev", agents_dir, global_raw)

    mensaje = str(exc.value)
    assert "channels.slack" in mensaje
    assert "canal desconocido" in mensaje
    assert "dev.yaml" in mensaje, "el error debe nombrar el fichero a corregir"


def test_los_flags_globales_no_contaminan_el_dict_de_adapters(tmp_path: Path) -> None:
    """``channels.thinking_indicator`` (global) comparte clave con el dict de
    adapters del agente. Sin el filtro, el merge lo colaría y abortaría el
    arranque como canal desconocido."""
    cfg_dir, agents_dir = _escribir_home(
        tmp_path, "id: dev\nname: Dev\ndescription: d\nchannels:\n  telegram:\n    token: T\n"
    )
    with (cfg_dir / "global.yaml").open("a", encoding="utf-8") as f:
        f.write("channels:\n  thinking_indicator: true\n")
    _, global_raw = load_global_config(cfg_dir)

    agente = load_agent_config("dev", agents_dir, global_raw)

    assert agente is not None
    assert set(agente.channels) == {"telegram"}
