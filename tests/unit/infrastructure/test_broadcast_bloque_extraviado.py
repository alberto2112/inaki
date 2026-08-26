"""
Tests del aviso por bloque ``broadcast:`` escrito en un nivel que nadie lee.

Caso real (2026-08-15): el operador tenía

    broadcast:
      auth: "..."
      port: 14863

fuera de ``channels.telegram``. ``assemble_agent_config`` solo copia ``channels``,
así que la clave se descartaba y el wiring ni se enteraba: el daemon arrancaba
sano, el puerto cerrado, y **cero logs a cualquier nivel** — ni siquiera el error
de topología vieja, porque el bloque nunca llegó al parser.

El contrato: si hay un ``broadcast:`` en un path que el wiring no lee, sale un
WARNING nombrando el path válido.
"""

from __future__ import annotations

import logging

import pytest
import yaml

from core.domain.errors import ConfigError
from infrastructure.config import load_agent_config


GLOBAL_MINIMO = {
    "llm": {"provider": "openai", "model": "gpt-4o-mini"},
    "embedding": {"provider": "e5_onnx"},
}

BLOQUE = {"auth": "alberto", "port": 14863}


def _escribir_agente(agents_dir, cuerpo: dict) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    base = {"id": "inaki", "name": "Inaki", "description": "agente de test"}
    (agents_dir / "inaki.yaml").write_text(yaml.safe_dump({**base, **cuerpo}), encoding="utf-8")


@pytest.mark.parametrize(
    "cuerpo, esperado, sigue_cargando",
    [
        pytest.param(
            {"channels": {"telegram": {"token": "t"}}, "broadcast": BLOQUE},
            "broadcast (raíz del agente)",
            True,
            id="raiz-del-agente",
        ),
        pytest.param(
            {"channels": {"telegram": {"token": "t"}, "broadcast": BLOQUE}},
            "channels.broadcast",
            False,
            id="channels-broadcast",
        ),
    ],
)
def test_bloque_extraviado_avisa(tmp_path, caplog, cuerpo, esperado, sigue_cargando):
    caplog.set_level(logging.WARNING, logger="infrastructure.config_loader")
    agents_dir = tmp_path / "agents"
    _escribir_agente(agents_dir, cuerpo)

    # Desde el chequeo top-level, AMBAS ubicaciones abortan: la raíz del agente
    # es una clave desconocida y ``channels.broadcast`` un canal desconocido.
    # El aviso corre ANTES del abort: el operador lee el path válido Y el error.
    del sigue_cargando  # el parámetro documenta la conducta vieja; hoy ninguno carga
    with pytest.raises(ConfigError):
        load_agent_config("inaki", agents_dir, dict(GLOBAL_MINIMO))
    assert esperado in caplog.text
    assert "channels.telegram.broadcast" in caplog.text, (
        f"el aviso debe nombrar el path válido; dijo: {caplog.text!r}"
    )


def test_bloque_en_el_path_valido_no_avisa(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="infrastructure.config_loader")
    agents_dir = tmp_path / "agents"
    _escribir_agente(
        agents_dir,
        {
            "channels": {
                "telegram": {"token": "t", "broadcast": {"auth": "a", "server": {"port": 14863}}}
            }
        },
    )

    cfg = load_agent_config("inaki", agents_dir, dict(GLOBAL_MINIMO))

    assert cfg is not None
    assert "NADIE lee" not in caplog.text


def test_sin_broadcast_no_avisa(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="infrastructure.config_loader")
    agents_dir = tmp_path / "agents"
    _escribir_agente(agents_dir, {"channels": {"telegram": {"token": "t"}}})

    cfg = load_agent_config("inaki", agents_dir, dict(GLOBAL_MINIMO))

    assert cfg is not None
    assert "NADIE lee" not in caplog.text
