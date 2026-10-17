"""Camino dorado 4: ``config_set`` de punta a punta, con las piezas REALES.

Un turno de operador le pide al agente cambiar su modelo; el cambio se valida con el
loader y las factories reales, queda en el store EN MEMORIA, pide un reload que lo
APLICA, y el re-bootstrap (el mismo camino que ``bootstrap_fn``) levanta un runtime
que corre con el valor nuevo y lo reporta con el origen ``runtime``. Un ``/reload``
manual lo devuelve al disco y avisa qué descartó.

Solo se falsean los bordes externos (LLM, embedder, API de Telegram). El LLM falso
sigue un guion: pide la tool y después contesta con texto.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from inaki.kernel.domain.llm_response import LLMResponse
from inaki.shared.channel_context import ChannelContext

from .conftest import AGENT_ID, USER_ID, FakeLLM


class GuionLLM(FakeLLM):
    """Responde con las respuestas del guion en orden; agotado, texto plano."""

    def __init__(self, guion: list[LLMResponse] | None = None) -> None:
        super().__init__("(fin del guion)")
        self.guion = list(guion or [])

    async def complete(self, messages, system_prompt, tools=None):
        respuesta = await super().complete(messages, system_prompt, tools)
        return self.guion.pop(0) if self.guion else respuesta


def pedir_config_set(path: str, value: object) -> LLMResponse:
    return LLMResponse(
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "config_set",
                    "arguments": json.dumps({"path": path, "value": value}),
                },
            }
        ]
    )


@pytest.fixture
def fake_llm() -> GuionLLM:
    return GuionLLM()


@pytest.fixture
def store():
    from inaki.config.runtime_overrides import OverridesDeRuntime

    return OverridesDeRuntime()


def _ensamblar(config_files, store):
    from inaki.app.assembly import ensamblar
    from inaki.config import AgentRegistry, ensure_user_config, load_global_config

    config_dir, agents_dir = config_files
    ensure_user_config(config_dir, agents_dir)
    global_config, global_raw = load_global_config(config_dir)
    registry = AgentRegistry(agents_dir, global_raw)
    return ensamblar(global_config, registry, config_dir=config_dir, overrides=store)


@pytest.fixture
def harness(config_files, bordes_externos, store):
    """El composition root REAL con el store del proceso, como ``run_daemon_mode``."""
    return _ensamblar(config_files, store)


def _rebootstrap(config_files, store):
    """Lo que hace ``bootstrap_fn`` tras un reload: bootstrap con el store + ensamblar."""
    from inaki.app.assembly import ensamblar
    from inaki.app.bootstrap import bootstrap

    config_dir, agents_dir = config_files
    global_config, registry = bootstrap(config_dir, agents_dir, overrides=store)
    return ensamblar(global_config, registry, config_dir=config_dir, overrides=store)


def _update_privado(texto: str) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = int(USER_ID)
    update.effective_chat.id = int(USER_ID)
    update.effective_chat.type = "private"
    update.message.chat.id = int(USER_ID)
    update.message.chat.type = "private"
    update.message.text = texto
    update.message.location = None
    update.message.from_user.id = int(USER_ID)
    update.message.from_user.username = "alberto"
    update.message.from_user.first_name = "Alberto"
    update.message.from_user.last_name = None
    update.message.reply_text = AsyncMock(return_value=None)
    update.message.set_reaction = AsyncMock(return_value=None)
    return update


@pytest.fixture
def bot(harness):
    from inaki.channels.telegram.bot import TelegramBot
    from inaki.channels.telegram.wiring import build_telegram_bot_ports, build_telegram_bot_settings

    agente = harness.get_agent(AGENT_ID)
    with patch("inaki.channels.telegram.bot.Application"):
        return TelegramBot(
            build_telegram_bot_settings(agente.agent_config),
            build_telegram_bot_ports(agente),
        )


async def _config_get(runtime, path: str) -> str:
    resultado = await runtime.get_agent(AGENT_ID).tools.execute(
        "config", operation="get", path=path
    )
    return resultado.output


async def test_privado_del_operador_acepta_pide_reload_y_el_reinicio_lo_aplica(
    config_files, harness, store, bot, fake_llm: GuionLLM
) -> None:
    fake_llm.guion = [
        pedir_config_set("llm.model", "modelo-nuevo"),
        LLMResponse.of_text("Listo, me reinicio con modelo-nuevo."),
    ]
    update = _update_privado("cambiá tu modelo a modelo-nuevo")

    await bot._handle_message(update, context=None)

    # La respuesta del turno SE ENTREGÓ, y el reload ya estaba pedido en modo "aplicar".
    assert "me reinicio con modelo-nuevo" in update.message.reply_text.await_args.args[0]
    assert harness.reloader.was_triggered()
    assert harness.reloader.modo == "aplicar"
    assert store.paths_runtime(AGENT_ID) == {"llm.model"}

    # El re-bootstrap (mismo camino que `bootstrap_fn`) corre con el valor nuevo.
    nuevo = _rebootstrap(config_files, store)
    assert nuevo.get_agent(AGENT_ID).agent_config.llm.model == "modelo-nuevo"
    leido = await _config_get(nuevo, "llm.model")
    assert "'modelo-nuevo'" in leido and "[runtime]" in leido

    # Con los canales arriba, el hook de arranque avisa al chat que lo pidió (una vez).
    from inaki.app.bootstrap import avisos_post_arranque
    from inaki.kernel.domain.outbound_kind import OutboundKind

    telegram = MagicMock()
    telegram.channel_name = "telegram"
    telegram.capabilities.return_value = {OutboundKind.TEXT}
    telegram.send = AsyncMock()
    nuevo.get_agent(AGENT_ID).channel_outbound_registry.register(telegram)

    await avisos_post_arranque(store)(nuevo)
    await avisos_post_arranque(store)(nuevo)

    telegram.send.assert_awaited_once()
    assert telegram.send.await_args is not None
    enviado = telegram.send.await_args.kwargs
    assert (enviado["chat_id"], enviado["kind"]) == (USER_ID, OutboundKind.TEXT)
    assert "llm.model = 'modelo-nuevo'" in enviado["text"]


async def test_el_reload_manual_devuelve_al_disco_y_avisa_que_descarto(
    config_files, harness, store, bot, fake_llm: GuionLLM
) -> None:
    fake_llm.guion = [pedir_config_set("llm.model", "modelo-nuevo"), LLMResponse.of_text("ok")]
    await bot._handle_message(_update_privado("cambiá tu modelo"), context=None)
    nuevo = _rebootstrap(config_files, store)
    assert nuevo.get_agent(AGENT_ID).agent_config.llm.model == "modelo-nuevo"

    descartados = nuevo.reloader.request_reload()

    assert descartados == [f"{AGENT_ID}: llm.model = 'modelo-nuevo'"]
    assert store.tomar_avisos() == []  # el aviso de éxito tampoco corresponde: no se aplicó
    assert nuevo.reloader.modo == "descartar"
    vuelto = _rebootstrap(config_files, store)
    assert vuelto.get_agent(AGENT_ID).agent_config.llm.model == "modelo-falso"
    assert "[runtime]" not in await _config_get(vuelto, "llm.model")


async def test_en_un_grupo_se_rechaza_sin_tocar_el_store_ni_pedir_reload(
    harness, store, fake_llm: GuionLLM
) -> None:
    fake_llm.guion = [
        pedir_config_set("llm.model", "modelo-nuevo"),
        LLMResponse.of_text("no puedo"),
    ]
    grupo = ChannelContext(channel_type="telegram", user_id=AGENT_ID, chat_id="-1001")

    respuesta = await harness.get_agent(AGENT_ID).run_agent.execute(
        "cambiá tu modelo", ctx=grupo, es_operador=False
    )

    assert respuesta == "no puedo"
    assert store.paths_runtime(AGENT_ID) == frozenset()
    assert not harness.reloader.was_triggered()
    visto_por_el_llm = " ".join(str(m.content) for m in fake_llm.llamadas[1]["messages"])
    assert "Accepted" not in visto_por_el_llm


async def test_un_provider_inexistente_lo_rechaza_la_verificacion_de_adapters(
    harness, store, fake_llm: GuionLLM
) -> None:
    fake_llm.guion = [
        pedir_config_set("llm.provider", "inexistente"),
        LLMResponse.of_text("no se pudo"),
    ]
    operador = ChannelContext(channel_type="telegram", user_id=USER_ID, chat_id=USER_ID)

    await harness.get_agent(AGENT_ID).run_agent.execute(
        "usá otro proveedor", ctx=operador, es_operador=True
    )

    visto_por_el_llm = " ".join(str(m.content) for m in fake_llm.llamadas[1]["messages"])
    assert "Rejected, nothing was changed" in visto_por_el_llm
    assert "inexistente" in visto_por_el_llm
    assert store.paths_runtime(AGENT_ID) == frozenset()
    assert not harness.reloader.was_triggered()


async def test_un_secreto_no_se_cambia_ni_en_un_turno_de_operador(
    harness, store, fake_llm: GuionLLM
) -> None:
    fake_llm.guion = [
        pedir_config_set("providers.openrouter.api_key", "sk-robada"),
        LLMResponse.of_text("no"),
    ]
    operador = ChannelContext(channel_type="telegram", user_id=USER_ID, chat_id=USER_ID)

    await harness.get_agent(AGENT_ID).run_agent.execute(
        "cambiá tu api key", ctx=operador, es_operador=True
    )

    visto_por_el_llm = " ".join(str(m.content) for m in fake_llm.llamadas[1]["messages"])
    assert "cannot be changed at runtime" in visto_por_el_llm
    assert store.paths_runtime(AGENT_ID) == frozenset()
    assert not harness.reloader.was_triggered()
