"""Capa de overrides de runtime en el loader (tarea 5 de ``config-set-efimero``).

Orden efectivo: ``global.yaml → runtime-global → [extra_base] → agents/{id}.yaml →
runtime-agente``. El override se valida con el MISMO schema que el YAML y se
mergea con el motor único; sin override el comportamiento es idéntico al de antes.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from inaki.config import AgentRegistry, ensure_user_config, load_agent_config, load_global_config
from inaki.config.home import set_inaki_home
from inaki.config.runtime_overrides import EntradaOverride, OverridesDeRuntime
from inaki.shared.errors import ConfigError


@pytest.fixture
def dirs(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    """``(config_dir, agents_dir)`` de una instancia aislada, con el ``global.yaml`` por defecto."""
    set_inaki_home(tmp_path)
    config_dir, agents_dir = tmp_path / "config", tmp_path / "agents"
    ensure_user_config(config_dir, agents_dir)
    yield config_dir, agents_dir
    set_inaki_home(None)


def _agente(agents_dir: Path, agent_id: str, extra: str = "", *, sub: bool = False) -> None:
    destino = agents_dir / "sub-agents" if sub else agents_dir
    destino.mkdir(parents=True, exist_ok=True)
    (destino / f"{agent_id}.yaml").write_text(
        f"id: {agent_id}\nname: {agent_id}\ndescription: agente de prueba\n{extra}",
        encoding="utf-8",
    )


def _global_yaml(config_dir: Path, bloque: str, valor: dict) -> None:
    """Pisa un bloque top-level del ``global.yaml`` generado por defecto."""
    ruta = config_dir / "global.yaml"
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    datos[bloque] = valor
    ruta.write_text(yaml.safe_dump(datos), encoding="utf-8")


class TestLoadGlobalConfigConRuntime:
    def test_el_override_pisa_el_valor_del_yaml(self, dirs):
        config_dir, _ = dirs
        base, _ = load_global_config(config_dir)
        assert base.app.log_level == "INFO"  # el default del global.yaml generado

        gc, _ = load_global_config(config_dir, runtime={"app": {"log_level": "DEBUG"}})

        assert gc.app.log_level == "DEBUG"

    def test_el_override_se_funde_con_el_bloque_del_yaml(self, dirs):
        config_dir, _ = dirs
        base, _ = load_global_config(config_dir)

        gc, _ = load_global_config(config_dir, runtime={"app": {"log_level": "DEBUG"}})

        # Solo cambia la clave del override: el resto del bloque se hereda del YAML.
        assert gc.app.name == base.app.name
        assert gc.app.log_format == base.app.log_format

    def test_el_global_raw_devuelto_incluye_el_override(self, dirs):
        # Es la base de los agentes: el override global tiene que llegarles.
        config_dir, _ = dirs

        _, global_raw = load_global_config(config_dir, runtime={"llm": {"temperature": 0.25}})

        assert global_raw["llm"]["temperature"] == 0.25

    def test_sin_runtime_o_con_runtime_vacio_es_identico(self, dirs):
        config_dir, _ = dirs
        sin_arg = load_global_config(config_dir)

        con_none = load_global_config(config_dir, runtime=None)
        con_vacio = load_global_config(config_dir, runtime={})

        assert con_none[0].model_dump() == sin_arg[0].model_dump()
        assert con_vacio[0].model_dump() == sin_arg[0].model_dump()
        assert con_vacio[1] == sin_arg[1]

    def test_una_clave_desconocida_en_el_override_se_rechaza_nombrando_la_capa(self, dirs):
        config_dir, _ = dirs

        with pytest.raises(ConfigError, match="override de runtime.*'llmm'.*llm"):
            load_global_config(config_dir, runtime={"llmm": {"model": "x"}})

    def test_un_bloque_de_agente_no_entra_por_la_capa_global(self, dirs):
        # `id` es de AgentConfig: la capa runtime-global es solo GlobalConfig.
        config_dir, _ = dirs

        with pytest.raises(ConfigError, match="override de runtime.*'id'"):
            load_global_config(config_dir, runtime={"id": "otro"})

    def test_un_campo_desconocido_dentro_de_un_bloque_lo_rechaza_el_schema(self, dirs):
        # El mismo `extra="forbid"` que valida el YAML valida el override.
        config_dir, _ = dirs

        with pytest.raises(ValidationError, match="log_levl.*Quisiste decir 'log_level'"):
            load_global_config(config_dir, runtime={"app": {"log_levl": "DEBUG"}})

    def test_un_escalar_sobre_un_bloque_es_conflicto_de_forma_nombrando_la_capa(self, dirs):
        config_dir, _ = dirs

        with pytest.raises(ConfigError, match=r"Conflicto de tipos.*runtime"):
            load_global_config(config_dir, runtime={"app": "x"})

    def test_cargar_con_un_override_de_knowledge_no_vacia_el_store(self, dirs):
        # Regresión: `load_global_config` hace `knowledge_raw.pop("sources")` sobre el dict
        # mergeado, y `deep_merge` pone el override POR REFERENCIA. El store entrega copias.
        config_dir, _ = dirs
        fuente = {"id": "docs", "type": "document", "path": "/tmp/docs"}
        store = OverridesDeRuntime()
        store.agregar(EntradaOverride.para("a", "knowledge", {"sources": [fuente]}), None)

        gc, _ = load_global_config(config_dir, runtime=store.capa_global())

        assert [s.id for s in gc.knowledge.sources] == ["docs"]  # el loader SÍ la vio
        assert store.capa_global() == {"knowledge": {"sources": [fuente]}}  # y el store la conserva


class TestLoadAgentConfigConRuntime:
    def test_el_override_pisa_el_yaml_del_agente(self, dirs):
        config_dir, agents_dir = dirs
        _agente(agents_dir, "dev", "llm:\n  model: del-agente\n")
        _, global_raw = load_global_config(config_dir)

        sin = load_agent_config("dev", agents_dir, global_raw)
        con = load_agent_config(
            "dev", agents_dir, global_raw, runtime={"llm": {"model": "de-runtime"}}
        )

        assert sin is not None and sin.llm.model == "del-agente"
        assert con is not None and con.llm.model == "de-runtime"

    def test_el_override_conserva_lo_que_no_pisa(self, dirs):
        config_dir, agents_dir = dirs
        _agente(agents_dir, "dev", "llm:\n  model: del-agente\n  temperature: 0.7\n")
        _, global_raw = load_global_config(config_dir)

        cfg = load_agent_config(
            "dev", agents_dir, global_raw, runtime={"llm": {"model": "de-runtime"}}
        )

        assert cfg is not None
        assert (cfg.llm.model, cfg.llm.temperature) == ("de-runtime", 0.7)

    def test_el_override_de_runtime_gana_al_yaml_del_agente_y_este_al_del_global(self, dirs):
        config_dir, agents_dir = dirs
        _global_yaml(config_dir, "tools", {"semantic_routing_top_k": 2})
        _agente(agents_dir, "dev", "tools:\n  semantic_routing_top_k: 4\n")
        _, global_raw = load_global_config(config_dir)

        sin = load_agent_config("dev", agents_dir, global_raw)
        con = load_agent_config(
            "dev", agents_dir, global_raw, runtime={"tools": {"semantic_routing_top_k": 9}}
        )

        assert sin is not None and sin.tools.semantic_routing_top_k == 4
        assert con is not None and con.tools.semantic_routing_top_k == 9

    def test_una_clave_desconocida_se_rechaza_nombrando_la_capa(self, dirs):
        config_dir, agents_dir = dirs
        _agente(agents_dir, "dev")
        _, global_raw = load_global_config(config_dir)

        with pytest.raises(ConfigError, match="override de runtime.*'llmm'"):
            load_agent_config("dev", agents_dir, global_raw, runtime={"llmm": {"model": "x"}})

    def test_un_bloque_harness_global_no_entra_por_la_capa_del_agente(self, dirs):
        # `user` es GlobalConfig-only: por la capa del agente es un error, no un override mudo.
        config_dir, agents_dir = dirs
        _agente(agents_dir, "dev")
        _, global_raw = load_global_config(config_dir)

        with pytest.raises(ConfigError, match="override de runtime.*GLOBAL"):
            load_agent_config("dev", agents_dir, global_raw, runtime={"user": {"timezone": "UTC"}})

    def test_no_muta_el_global_raw_recibido(self, dirs):
        config_dir, agents_dir = dirs
        _agente(agents_dir, "dev")
        _, global_raw = load_global_config(config_dir)
        antes = copy.deepcopy(global_raw)

        load_agent_config("dev", agents_dir, global_raw, runtime={"llm": {"model": "x"}})

        assert global_raw == antes

    def test_sin_runtime_el_agente_es_identico(self, dirs):
        config_dir, agents_dir = dirs
        _agente(agents_dir, "dev", "llm:\n  model: m\n")
        _, global_raw = load_global_config(config_dir)

        sin = load_agent_config("dev", agents_dir, global_raw)
        con_none = load_agent_config("dev", agents_dir, global_raw, runtime=None)

        assert sin is not None and con_none is not None
        assert con_none.model_dump() == sin.model_dump()


class TestAgentRegistryConRuntime:
    def test_aplica_la_capa_solo_al_agente_dueno(self, dirs):
        config_dir, agents_dir = dirs
        _agente(agents_dir, "alpha")
        _agente(agents_dir, "beta")
        _, global_raw = load_global_config(config_dir)

        registry = AgentRegistry(
            agents_dir, global_raw, runtime_por_agente={"alpha": {"llm": {"model": "rt"}}}
        )

        assert registry.get("alpha").llm.model == "rt"
        assert registry.get("beta").llm.model != "rt"

    def test_un_sub_agente_no_recibe_overrides_aunque_el_id_coincida(self, dirs):
        config_dir, agents_dir = dirs
        _agente(agents_dir, "alpha")
        _agente(agents_dir, "helper", sub=True)
        _, global_raw = load_global_config(config_dir)

        registry = AgentRegistry(
            agents_dir, global_raw, runtime_por_agente={"helper": {"llm": {"model": "rt"}}}
        )

        assert registry.is_sub_agent("helper")
        assert registry.get("helper").llm.model != "rt"

    def test_un_agent_id_sin_fichero_en_el_mapa_se_ignora(self, dirs):
        config_dir, agents_dir = dirs
        _agente(agents_dir, "alpha")
        _, global_raw = load_global_config(config_dir)

        registry = AgentRegistry(
            agents_dir, global_raw, runtime_por_agente={"fantasma": {"llm": {"model": "rt"}}}
        )

        assert [a.id for a in registry.list_all()] == ["alpha"]

    def test_el_override_invalido_de_un_agente_aborta_la_carga(self, dirs):
        config_dir, agents_dir = dirs
        _agente(agents_dir, "alpha")
        _, global_raw = load_global_config(config_dir)

        with pytest.raises(ConfigError, match="override de runtime"):
            AgentRegistry(agents_dir, global_raw, runtime_por_agente={"alpha": {"nope": 1}})

    def test_sin_runtime_por_agente_es_identico(self, dirs):
        config_dir, agents_dir = dirs
        _agente(agents_dir, "alpha")
        _, global_raw = load_global_config(config_dir)

        sin = AgentRegistry(agents_dir, global_raw)
        con = AgentRegistry(agents_dir, global_raw, runtime_por_agente=None)

        assert con.get("alpha").model_dump() == sin.get("alpha").model_dump()


class TestBootstrapConOverrides:
    def test_el_store_llega_al_global_y_a_los_agentes(self, dirs):
        from inaki.app.bootstrap import bootstrap

        config_dir, agents_dir = dirs
        _agente(agents_dir, "alpha")
        store = OverridesDeRuntime()
        store.agregar(EntradaOverride.para("alpha", "llm.model", "rt-modelo"), None)
        store.agregar(EntradaOverride.para("alpha", "user.timezone", "Asia/Tokyo"), None)

        gc, registry = bootstrap(config_dir, agents_dir, overrides=store)

        assert registry.get("alpha").llm.model == "rt-modelo"
        assert gc.user.timezone == "Asia/Tokyo"

    def test_un_override_per_agente_no_se_filtra_a_los_otros_agentes(self, dirs):
        from inaki.app.bootstrap import bootstrap

        config_dir, agents_dir = dirs
        _agente(agents_dir, "alpha")
        _agente(agents_dir, "beta")
        store = OverridesDeRuntime()
        # `llm.temperature` es de AgentConfig: va a la capa de alpha y beta no se entera.
        store.agregar(EntradaOverride.para("alpha", "llm.temperature", 0.11), None)

        _, registry = bootstrap(config_dir, agents_dir, overrides=store)

        assert registry.get("alpha").llm.temperature == 0.11
        assert registry.get("beta").llm.temperature != 0.11  # per-agente: beta no se entera

    def test_sin_overrides_el_bootstrap_es_el_de_siempre(self, dirs):
        from inaki.app.bootstrap import bootstrap

        config_dir, agents_dir = dirs
        _agente(agents_dir, "alpha", "llm:\n  model: del-yaml\n")

        _, registry = bootstrap(config_dir, agents_dir)
        _, registry_con_store_vacio = bootstrap(
            config_dir, agents_dir, overrides=OverridesDeRuntime()
        )

        assert registry.get("alpha").llm.model == "del-yaml"
        assert registry_con_store_vacio.get("alpha").llm.model == "del-yaml"
