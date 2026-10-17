"""``ConfigSetTool`` — el contrato que ve el LLM: schema, privilegio y mensajes.

El use case real corre con un ``validar`` simulado (base ⊕ capas del store); lo que se
prueba acá es la traducción de sus veredictos al texto de la tool y la decisión de a qué
chat se avisa.
"""

from __future__ import annotations

import pytest

from inaki.config.merge import deep_merge
from inaki.config.runtime_overrides import OverridesDeRuntime
from inaki.config.tools.config_set_tool import ConfigSetTool
from inaki.config.use_cases.runtime_overrides import (
    ConstruccionRechazadaError,
    GestionarOverridesUseCase,
)
from inaki.shared.channel_context import ChannelContext
from inaki.shared.errors import ConfigError

AGENTE = "anacleto"
BASE = {
    "llm": {"model": "a", "temperature": 0.7},
    "user": {"timezone": "UTC"},
    "embedding": {"model_dirname": "e5"},
}


class FakeReloader:
    modo = None

    def __init__(self) -> None:
        self.pedidos = 0

    def was_triggered(self) -> bool:
        return self.pedidos > 0

    def request_reload_para_aplicar(self) -> None:
        self.pedidos += 1


def _tool(
    *,
    validar=None,
    contexto: ChannelContext | None = None,
    tiene_outbound=lambda canal: True,
    modo=None,
):
    store = OverridesDeRuntime()
    reloader = FakeReloader()
    reloader.modo = modo
    uc = GestionarOverridesUseCase(
        agent_id=AGENTE,
        overrides=store,
        reloader=reloader,
        validar=validar
        or (lambda c: deep_merge(deep_merge(BASE, c.capa_global()), c.capa_agente(AGENTE))),
        paths_fijos=frozenset({"embedding", "embedding.model_dirname"}),
    )
    tool = ConfigSetTool(uc, get_channel_context=lambda: contexto, tiene_outbound=tiene_outbound)
    return tool, store, reloader


def _privado(chat: str = "123") -> ChannelContext:
    return ChannelContext(channel_type="telegram", user_id=chat, chat_id=chat)


# -- contrato estático ------------------------------------------------------------


def test_es_privilegiada_y_se_llama_config_set():
    tool, _, _ = _tool()

    assert tool.name == "config_set"
    assert tool.requires_operator is True


def test_el_schema_pide_path_y_value_y_no_tiene_operation():
    tool, _, _ = _tool()

    schema = tool.parameters_schema
    assert schema["required"] == ["path", "value"]
    assert set(schema["properties"]) == {"path", "value"}
    assert "null" in schema["properties"]["value"]["type"]


def test_la_description_va_en_ingles_y_avisa_que_es_solo_del_operador():
    tool, _, _ = _tool()

    assert "Operator only" in tool.description
    assert "[runtime]" in tool.description
    assert "/reload" in tool.description


def test_hay_routing_keywords_multilingues():
    tool, _, _ = _tool()

    assert "cambiá tu modelo" in tool.routing_keywords
    assert "change your model" in tool.routing_keywords
    assert "mets la température" in tool.routing_keywords


# -- argumentos --------------------------------------------------------------------


async def test_sin_path_falla_sin_reintento():
    tool, _, reloader = _tool()

    r = await tool.execute(value=1)

    assert r.success is False and r.retryable is False
    assert r.output == "'path' is required."
    assert reloader.pedidos == 0


async def test_sin_value_falla_pero_null_es_un_valor():
    tool, store, reloader = _tool()

    faltante = await tool.execute(path="llm.model")
    assert faltante.success is False
    assert faltante.output == "'value' is required (use null to disable)."
    assert reloader.pedidos == 0

    nulo = await tool.execute(path="llm.model", value=None)
    assert nulo.success is True
    assert store.paths_runtime(AGENTE) == {"llm.model"}


# -- veredictos ----------------------------------------------------------------------


async def test_aceptado_dice_el_valor_nuevo_el_anterior_y_que_se_pierde():
    tool, store, reloader = _tool(contexto=_privado())

    r = await tool.execute(path="llm.model", value="gpt-5-mini")

    assert r.success is True
    assert r.output.startswith("Accepted: llm.model = 'gpt-5-mini' (was 'a').")
    assert "restarts right after this reply" in r.output
    assert "confirmation here" in r.output
    assert "config files" in r.output
    assert "/reload" in r.output
    assert reloader.pedidos == 1
    assert store.paths_runtime(AGENTE) == {"llm.model"}


async def test_sin_chat_al_que_avisar_no_promete_confirmacion():
    tool, store, _ = _tool(contexto=None)

    r = await tool.execute(path="llm.model", value="x")

    assert r.success is True
    assert "confirmation here" not in r.output
    assert store.tomar_avisos() == []


async def test_registra_el_aviso_para_el_chat_de_origen():
    tool, store, _ = _tool(contexto=_privado("123"))

    await tool.execute(path="llm.model", value="x")

    (aviso,) = store.tomar_avisos()
    assert (aviso.agent_id, aviso.canal, aviso.chat_id) == (AGENTE, "telegram", "123")


async def test_un_canal_sin_outbound_no_recibe_aviso_ni_promesa():
    rest = ChannelContext(channel_type="rest", user_id="local")
    tool, store, _ = _tool(contexto=rest, tiene_outbound=lambda canal: canal == "telegram")

    r = await tool.execute(path="llm.model", value="x")

    assert r.success is True
    assert "confirmation here" not in r.output
    assert store.tomar_avisos() == []


async def test_en_un_grupo_el_aviso_va_al_chat_del_grupo():
    grupo = ChannelContext(channel_type="telegram", user_id="anacleto", chat_id="-1001")
    tool, store, _ = _tool(contexto=grupo)

    await tool.execute(path="llm.model", value="x")

    (aviso,) = store.tomar_avisos()
    assert aviso.chat_id == "-1001"


async def test_no_op_lo_dice_y_no_pide_reload():
    tool, store, reloader = _tool()

    r = await tool.execute(path="llm.model", value="a")

    assert r.success is True
    assert r.output == "llm.model is already 'a'. Nothing to change."
    assert reloader.pedidos == 0 and store.paths_runtime(AGENTE) == frozenset()


async def test_path_fijo_dice_que_no_se_puede_y_manda_a_editar_los_ficheros():
    tool, _, reloader = _tool()

    r = await tool.execute(path="embedding.model_dirname", value="x")

    assert r.success is False and r.retryable is False
    assert "'embedding.model_dirname' cannot be changed at runtime" in r.output
    assert "Edit the config files and restart" in r.output
    assert reloader.pedidos == 0


async def test_path_desconocido_sugiere_los_vecinos():
    tool, _, _ = _tool()

    r = await tool.execute(path="llm.modelo", value="x")

    assert r.success is False and r.retryable is False
    assert r.output == (
        "No configuration parameter at 'llm.modelo'. "
        "Did you mean one of: llm.model, llm.temperature?"
    )


async def test_path_desconocido_sin_vecinos_manda_a_la_tool_config():
    tool, _, _ = _tool()

    r = await tool.execute(path="zzz", value="x")

    assert "'config' tool" in r.output


async def test_el_loader_rechaza_y_se_nombra_el_error():
    def validar(candidato):
        if candidato.paths_runtime(AGENTE):
            raise ConfigError("llm.temperature: debe ser <= 2")
        return deep_merge(BASE, {})

    tool, store, reloader = _tool(validar=validar)

    r = await tool.execute(path="llm.temperature", value=9)

    assert r.success is False and r.retryable is False
    assert r.output.startswith("Rejected, nothing was changed: the configuration would not load")
    assert "debe ser <= 2" in r.output
    assert reloader.pedidos == 0 and store.paths_runtime(AGENTE) == frozenset()


async def test_la_verificacion_de_adapters_rechaza_con_su_mensaje():
    def validar(candidato):
        if candidato.paths_runtime(AGENTE):
            raise ConstruccionRechazadaError("Proveedor LLM 'foo' no encontrado. Disponibles: []")
        return deep_merge(BASE, {})

    tool, _, _ = _tool(validar=validar)

    r = await tool.execute(path="llm.model", value="x")

    assert r.output == (
        "Rejected, nothing was changed: Proveedor LLM 'foo' no encontrado. Disponibles: []"
    )


async def test_disco_roto_lo_distingue_del_cambio_pedido():
    def validar(candidato):
        raise ConfigError("agents/anacleto.yaml: YAML inválido")

    tool, _, _ = _tool(validar=validar)

    r = await tool.execute(path="llm.model", value="x")

    assert "config files on disk do not load right now" in r.output
    assert "were they edited without a reload?" in r.output
    assert "YAML inválido" in r.output


async def test_reload_manual_pendiente_pide_reintentar_despues_del_reinicio():
    tool, store, reloader = _tool(modo="descartar")

    r = await tool.execute(path="llm.model", value="x")

    assert r.success is False and r.retryable is False
    assert r.output == (
        "A manual reload is in progress and runtime changes are being discarded. "
        "Try again after the restart."
    )
    assert reloader.pedidos == 0 and store.paths_runtime(AGENTE) == frozenset()


@pytest.mark.parametrize("path", ["", "   ", None])
async def test_un_path_vacio_o_en_blanco_es_path_faltante(path):
    tool, _, _ = _tool()

    r = await tool.execute(path=path, value=1)

    assert r.output == "'path' is required."
