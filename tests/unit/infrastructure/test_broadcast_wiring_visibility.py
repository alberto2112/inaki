"""
Tests de observabilidad del wiring de broadcast en ``AppContainer``.

Historia del caso (``broadcast-arranque-observable``): ``_wire_broadcast_for_agent``
parseaba ``channels.telegram`` con Pydantic dentro de un ``try/except``. Si el
bloque no validaba —el caso típico es un ``broadcast:`` en el formato viejo
(``port:`` suelto o ``remote:``), o sin ``auth``— el método hacía ``return`` y se
llevaba puestos DOS recursos: el transporte TCP y el rate limiter de grupos. El
daemon seguía arrancando con el bot de Telegram online, así que desde afuera se
veía "todo bien" con el puerto cerrado. El contrato de entonces era que ese
camino fuese ruidoso: nivel ERROR con la topología esperada en el mensaje.

Hoy la garantía es MÁS FUERTE y llega antes: ``channels`` se valida contra
``CHANNEL_SCHEMAS`` en el propio ``AgentConfig``, así que una topología inválida
ni siquiera produce un ``AgentConfig`` — el arranque muere con el error de
validación y nunca se llega al wiring. Los tests de abajo fijan esa versión del
contrato: el bloque roto aborta la construcción, y el mensaje nombra el path del
canal para que el operador sepa dónde mirar.

El camino feliz se sigue ejerciendo sobre el método desestructurado con un
``self`` mínimo — construir un ``AppContainer`` real levantaría LLM, embeddings y
DBs sin aportar nada al caso.
"""

from __future__ import annotations

import types

import pytest
from pydantic import ValidationError

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
def test_topologia_invalida_aborta_la_construccion_del_agent_config(broadcast_raw):
    """Config que no valida ⇒ no hay ``AgentConfig``, así que no hay arranque.

    Antes este mismo bloque producía un ``AgentConfig`` sano y el fallo aparecía
    recién en el wiring, como un ERROR que el daemon se comía para seguir
    arrancando. Ahora muere en la construcción: es imposible que el operador vea
    el bot online con el puerto cerrado.
    """
    with pytest.raises(ValidationError) as exc_info:
        _agent_config({"token": "t", "broadcast": broadcast_raw})

    mensaje = str(exc_info.value)
    assert "channels.telegram" in mensaje, (
        "el error debe nombrar el path del canal para que el operador sepa qué "
        f"bloque revisar; dijo: {mensaje!r}"
    )


def test_topologia_invalida_no_llega_al_wiring_ni_con_grupos_autonomous():
    """El bloque roto se lleva el transporte Y el rate limiter — por eso aborta antes.

    Los dos recursos salen del MISMO parseo, así que un bloque inválido dejaba a
    un agente ``autonomous`` sin rate limiter y sin ninguna pista. El guard está
    ahora en la construcción del ``AgentConfig``: no hay config a medio validar
    que pueda llegar al wiring.
    """
    with pytest.raises(ValidationError) as exc_info:
        _agent_config(
            {
                "token": "t",
                "groups": {"behavior": "autonomous"},
                "broadcast": {"auth": "s" * 16, "port": 6499},  # formato viejo
            }
        )

    assert "channels.telegram" in str(exc_info.value)


def test_grupos_autonomous_validos_si_wirean_el_rate_limiter():
    """Control positivo: con la config sana el rate limiter de grupos se wirea.

    Cierra la pinza del test anterior — la garantía es "o wirea, o no arranca";
    nunca "arranca a medias".
    """
    self_ = _self_minimo()
    AppContainer._wire_broadcast_for_agent(
        self_,
        _agent_config(
            {
                "token": "t",
                "groups": {"behavior": "autonomous", "rate_limiter_window": 45},
                "broadcast": {"auth": "s" * 16, "server": {"port": 6499}},
            }
        ),
    )

    limiter = self_.agents["inaki"].group_rate_limiter
    assert limiter is not None, "un agente autonomous necesita rate limiter de grupos"
    assert limiter._window == 45.0
