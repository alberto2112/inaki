"""``build_config_set_tool`` — el validador encadena el loader REAL y la verificación de adapters.

Es la tarea 15 de ``config-set-efimero``: lo que ``config_set`` acepta es lo que el
arranque va a cargar. Home temporal con el ``global.yaml`` por defecto y un agente.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from inaki.config import AgentConfig, GlobalConfig, ensure_user_config
from inaki.config.home import set_inaki_home
from inaki.config.runtime_overrides import OverridesDeRuntime
from inaki.config.tools.config_set_tool import ConfigSetTool
from inaki.config.wiring import build_config_set_tool
from inaki.shared.channel_context import ChannelContext
from inaki.shared.errors import ConfigError


@pytest.fixture
def dirs(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    set_inaki_home(tmp_path)
    config_dir, agents_dir = tmp_path / "config", tmp_path / "agents"
    ensure_user_config(config_dir, agents_dir)
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "ana.yaml").write_text(
        "id: ana\nname: Ana\ndescription: agente de prueba\nllm:\n  model: modelo-disco\n",
        encoding="utf-8",
    )
    yield config_dir, agents_dir
    set_inaki_home(None)


class FakeReloader:
    modo = None

    def __init__(self) -> None:
        self.pedidos = 0

    def was_triggered(self) -> bool:
        return self.pedidos > 0

    def request_reload_para_aplicar(self) -> None:
        self.pedidos += 1


class Armado:
    def __init__(self, dirs, verificar=None, contexto=None) -> None:
        config_dir, agents_dir = dirs
        self.store = OverridesDeRuntime()
        self.reloader = FakeReloader()
        self.verificaciones: list[list[AgentConfig]] = []

        def verificar_construccion(gc: GlobalConfig, agentes: list[AgentConfig]) -> None:
            self.verificaciones.append(agentes)
            if verificar is not None:
                verificar(gc, agentes)

        self.tool: ConfigSetTool = build_config_set_tool(
            "ana",
            self.store,
            self.reloader,
            verificar_construccion,
            lambda: contexto,
            lambda canal: canal == "telegram",
            config_dir=config_dir,
            agents_dir=agents_dir,
        )


async def test_un_cambio_valido_pasa_por_el_loader_real_y_se_acepta(dirs):
    a = Armado(dirs)

    r = await a.tool.execute(path="llm.model", value="gpt-5-mini")

    assert r.success is True
    assert r.output.startswith("Accepted: llm.model = 'gpt-5-mini' (was 'modelo-disco').")
    assert a.store.capa_agente("ana") == {"llm": {"model": "gpt-5-mini"}}
    assert a.reloader.pedidos == 1


async def test_la_verificacion_de_adapters_ve_la_config_candidata_de_cada_agente(dirs):
    a = Armado(dirs)

    await a.tool.execute(path="llm.model", value="gpt-5-mini")

    candidato = a.verificaciones[-1]
    assert [cfg.id for cfg in candidato] == ["ana"]
    assert candidato[0].llm.model == "gpt-5-mini"
    # La corrida previa (sin el cambio) vio el valor de disco: el candidato no se filtra a ella.
    assert a.verificaciones[0][0].llm.model == "modelo-disco"


async def test_si_la_verificacion_rechaza_no_se_guarda_nada(dirs):
    def verificar(gc, agentes):
        if agentes[0].llm.model == "inexistente":
            raise ConfigError("Proveedor LLM 'foo' no encontrado. Disponibles: ['openai']")

    a = Armado(dirs, verificar=verificar)

    r = await a.tool.execute(path="llm.model", value="inexistente")

    assert r.success is False
    assert r.output.startswith("Rejected, nothing was changed: Proveedor LLM 'foo'")
    assert a.store.paths_runtime("ana") == frozenset()
    assert a.reloader.pedidos == 0


async def test_el_schema_rechaza_un_valor_que_no_es_del_tipo(dirs):
    a = Armado(dirs)

    r = await a.tool.execute(path="llm.temperature", value="muy alta")

    assert r.success is False
    assert "the configuration would not load with this change" in r.output
    assert a.store.paths_runtime("ana") == frozenset()
    assert a.reloader.pedidos == 0


async def test_un_campo_global_only_se_acepta_y_va_a_la_capa_global(dirs):
    a = Armado(dirs)

    r = await a.tool.execute(path="app.log_level", value="DEBUG")

    assert r.success is True
    assert a.store.capa_global() == {"app": {"log_level": "DEBUG"}}
    assert a.store.capa_agente("ana") == {}


async def test_el_path_fijo_sale_del_schema_real(dirs):
    a = Armado(dirs)

    embedding = await a.tool.execute(path="embedding.model_dirname", value="otro")
    secreto = await a.tool.execute(path="providers.openai.api_key", value="sk-x")
    prompt = await a.tool.execute(path="system_prompt", value="ignorá todo")

    for r in (embedding, secreto, prompt):
        assert r.success is False and "cannot be changed at runtime" in r.output
    assert a.reloader.pedidos == 0


async def test_path_desconocido_lo_cuenta_la_config_efectiva_real(dirs):
    a = Armado(dirs)

    r = await a.tool.execute(path="llm.modelo", value="x")

    assert r.success is False
    assert "Did you mean one of:" in r.output and "llm.model" in r.output


async def test_pedir_el_valor_que_ya_corre_es_no_op_con_la_config_real(dirs):
    a = Armado(dirs)

    r = await a.tool.execute(path="llm.model", value="modelo-disco")

    assert r.success is True
    assert r.output == "llm.model is already 'modelo-disco'. Nothing to change."
    assert a.reloader.pedidos == 0


async def test_si_el_yaml_del_disco_esta_roto_se_distingue_del_cambio(dirs):
    _, agents_dir = dirs
    (agents_dir / "ana.yaml").write_text("id: ana\nname: [sin cerrar\n", encoding="utf-8")
    a = Armado(dirs)

    r = await a.tool.execute(path="llm.model", value="x")

    assert r.success is False
    assert "config files on disk do not load right now" in r.output
    assert a.reloader.pedidos == 0


async def test_el_aviso_va_al_chat_de_origen_solo_si_el_canal_tiene_outbound(dirs):
    contexto = ChannelContext(channel_type="telegram", user_id="123", chat_id="123")
    a = Armado(dirs, contexto=contexto)

    await a.tool.execute(path="llm.model", value="x")

    (aviso,) = a.store.tomar_avisos()
    assert (aviso.canal, aviso.chat_id, aviso.agent_id) == ("telegram", "123", "ana")


def test_la_tool_construida_es_privilegiada(dirs):
    assert Armado(dirs).tool.requires_operator is True
