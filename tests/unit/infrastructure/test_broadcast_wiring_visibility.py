"""
Tests de observabilidad del wiring de broadcast en ``AppContainer``.

``_wire_broadcast_for_agent`` parsea ``channels.telegram`` con Pydantic. Si el
bloque no valida —el caso típico es un ``broadcast:`` en el formato viejo
(``port:`` suelto o ``remote:``), o sin ``auth``— el método hace ``return`` y se
lleva puestos DOS recursos: el transporte TCP y el rate limiter de grupos. El
daemon sigue arrancando con el bot de Telegram online, así que desde afuera se
ve "todo bien" con el puerto cerrado.

El contrato acá es que ese camino sea ruidoso: nivel ERROR y con la topología
esperada en el mensaje. Se invoca el método desestructurado sobre un ``self``
mínimo — construir un ``AppContainer`` real levantaría LLM, embeddings y DBs sin
aportar nada al caso.
"""

from __future__ import annotations

import logging
import types

import pytest

from infrastructure.config import (
    AgentConfig,
    ChatHistoryConfig,
    EmbeddingConfig,
    LLMConfig,
    MemoriesConfig,
)
from infrastructure.container import AppContainer


def _agent_config(telegram: dict) -> AgentConfig:
    return AgentConfig(
        id="inaki",
        name="Inaki",
        description="agente de test",
        llm=LLMConfig(),
        embedding=EmbeddingConfig(),
        memories=MemoriesConfig(),
        chat_history=ChatHistoryConfig(),
        channels={"telegram": telegram},
    )


def _self_minimo() -> types.SimpleNamespace:
    """``self`` con lo mínimo que toca ``_wire_broadcast_for_agent``."""
    return types.SimpleNamespace(
        agents={
            "inaki": types.SimpleNamespace(broadcast_adapter=None, group_rate_limiter=None),
        },
        _broadcast_adapters=[],
    )


def test_topologia_valida_wirea_el_adapter_server():
    self_ = _self_minimo()
    AppContainer._wire_broadcast_for_agent(
        self_,
        _agent_config(
            {"token": "t", "broadcast": {"auth": "s" * 16, "server": {"port": 6499}}},
        ),
    )

    assert len(self_._broadcast_adapters) == 1
    adapter = self_._broadcast_adapters[0]
    assert adapter._role == "server"
    assert adapter._host == "0.0.0.0"  # el server escucha en toda la LAN
    assert adapter._port == 6499
    assert self_.agents["inaki"].broadcast_adapter is adapter


@pytest.mark.parametrize(
    "broadcast_raw",
    [
        pytest.param({"auth": "s" * 16, "port": 6499}, id="formato-viejo-port"),
        pytest.param(
            {"remote": {"host": "192.168.1.50:6499", "auth": "s" * 16}}, id="formato-viejo-remote"
        ),
        pytest.param({"server": {"port": 6499}}, id="sin-auth"),
        pytest.param({"auth": "s" * 16, "server": {"port": 80}}, id="puerto-privilegiado"),
    ],
)
def test_topologia_invalida_loguea_error_y_no_wirea(broadcast_raw, caplog):
    """Config que no valida ⇒ ERROR accionable, no un WARNING enterrado."""
    caplog.set_level(logging.WARNING, logger="infrastructure.container")
    self_ = _self_minimo()

    AppContainer._wire_broadcast_for_agent(
        self_,
        _agent_config({"token": "t", "broadcast": broadcast_raw}),
    )

    assert self_._broadcast_adapters == []
    assert self_.agents["inaki"].broadcast_adapter is None

    errores = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errores, (
        "un broadcast declarado que no valida deja el puerto cerrado: tiene que "
        f"salir como ERROR. Registros: {caplog.text!r}"
    )
    mensaje = errores[0].getMessage()
    assert "broadcast.server.port" in mensaje and "broadcast.client.host" in mensaje, (
        f"el ERROR debe decir qué topología se espera; dijo: {mensaje!r}"
    )


def test_topologia_invalida_tambien_mata_el_rate_limiter_de_grupos(caplog):
    """El mismo return se lleva el rate limiter — el ERROR debe advertirlo."""
    caplog.set_level(logging.WARNING, logger="infrastructure.container")
    self_ = _self_minimo()

    AppContainer._wire_broadcast_for_agent(
        self_,
        _agent_config(
            {
                "token": "t",
                "groups": {"behavior": "autonomous"},
                "broadcast": {"auth": "s" * 16, "port": 6499},  # formato viejo
            }
        ),
    )

    assert self_.agents["inaki"].group_rate_limiter is None
    errores = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errores and "rate limiter" in errores[0].getMessage()
