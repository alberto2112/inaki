"""El hook de avisos: manda por el outbound del agente lo que ``config_set`` dejó pendiente.

Corre una vez por arranque, con el runtime NUEVO, y entrega cada aviso al chat que pidió
el cambio. Un fallo de envío es un WARNING: el cambio ya está aplicado, el aviso es
cortesía.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from inaki.app.bootstrap import avisos_post_arranque
from inaki.config.runtime_overrides import AvisoPendiente, EntradaOverride, OverridesDeRuntime
from inaki.kernel.domain.channel_outbound_registry import ChannelOutboundRegistry
from inaki.kernel.domain.outbound_kind import OutboundKind
from inaki.shared.errors import AgentNotFoundError


def _outbound(nombre: str = "telegram") -> MagicMock:
    outbound = MagicMock()
    outbound.channel_name = nombre
    outbound.capabilities.return_value = {OutboundKind.TEXT}
    outbound.send = AsyncMock()
    return outbound


def _runtime(**outbounds_por_agente: MagicMock) -> MagicMock:
    agentes = {}
    for agent_id, outbound in outbounds_por_agente.items():
        registry = ChannelOutboundRegistry()
        registry.register(outbound)
        agentes[agent_id] = MagicMock(channel_outbound_registry=registry)
    runtime = MagicMock()

    def get_agent(agent_id: str):
        if agent_id not in agentes:
            raise AgentNotFoundError(agent_id)
        return agentes[agent_id]

    runtime.get_agent.side_effect = get_agent
    return runtime


def _aviso(agent_id="a", canal="telegram", chat_id="123", texto="volví") -> AvisoPendiente:
    return AvisoPendiente(agent_id=agent_id, canal=canal, chat_id=chat_id, texto=texto)


def _store(*avisos: AvisoPendiente) -> OverridesDeRuntime:
    store = OverridesDeRuntime()
    for i, aviso in enumerate(avisos):
        store.agregar(EntradaOverride.para(aviso.agent_id, "llm.model", f"modelo-{i}"), aviso)
    return store


async def test_cada_aviso_sale_por_el_outbound_de_su_agente_al_chat_de_origen():
    tg_a, tg_b = _outbound(), _outbound()
    store = _store(
        _aviso("a", chat_id="123", texto="uno"), _aviso("b", chat_id="-100", texto="dos")
    )

    await avisos_post_arranque(store)(_runtime(a=tg_a, b=tg_b))

    tg_a.send.assert_awaited_once_with(
        chat_id="123", kind=OutboundKind.TEXT, text="uno", record_history=False
    )
    tg_b.send.assert_awaited_once_with(
        chat_id="-100", kind=OutboundKind.TEXT, text="dos", record_history=False
    )


async def test_dos_avisos_del_mismo_chat_salen_en_orden():
    tg = _outbound()
    store = _store(_aviso(texto="primero"), _aviso(texto="segundo"))

    await avisos_post_arranque(store)(_runtime(a=tg))

    assert [c.kwargs["text"] for c in tg.send.await_args_list] == ["primero", "segundo"]


async def test_los_avisos_se_entregan_una_sola_vez():
    tg = _outbound()
    store = _store(_aviso())
    hook = avisos_post_arranque(store)

    await hook(_runtime(a=tg))
    await hook(_runtime(a=tg))

    assert tg.send.await_count == 1


async def test_sin_avisos_no_manda_nada():
    tg = _outbound()

    await avisos_post_arranque(OverridesDeRuntime())(_runtime(a=tg))

    tg.send.assert_not_awaited()


async def test_un_send_que_levanta_es_warning_y_no_corta_los_demas(caplog):
    roto, sano = _outbound(), _outbound()
    roto.send.side_effect = RuntimeError("telegram caído")
    store = _store(_aviso("a", texto="uno"), _aviso("b", texto="dos"))

    with caplog.at_level(logging.WARNING):
        await avisos_post_arranque(store)(_runtime(a=roto, b=sano))

    sano.send.assert_awaited_once()
    assert any(
        r.levelno == logging.WARNING and "telegram caído" in r.getMessage() + str(r.exc_info)
        for r in caplog.records
    )


@pytest.mark.parametrize(
    "aviso, razon",
    [
        (_aviso(agent_id="fantasma"), "fantasma"),  # el agente ya no existe
        (_aviso(canal="slack"), "slack"),  # el agente no tiene ese canal
    ],
)
async def test_un_destino_inexistente_es_warning_que_nombra_el_destino(aviso, razon, caplog):
    tg = _outbound()
    store = _store(aviso, _aviso(texto="el sano"))

    with caplog.at_level(logging.WARNING):
        await avisos_post_arranque(store)(_runtime(a=tg))

    tg.send.assert_awaited_once_with(
        chat_id="123", kind=OutboundKind.TEXT, text="el sano", record_history=False
    )
    assert razon in caplog.text
