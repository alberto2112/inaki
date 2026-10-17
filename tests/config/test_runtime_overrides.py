"""Store de overrides de runtime (tarea 4 de ``config-set-efimero``).

Dominio puro del módulo config: una lista ORDENADA de entradas que se materializan
en capas con el motor único de merge (``inaki/config/merge.py``), más los avisos
pendientes para el chat que pidió el cambio.
"""

from __future__ import annotations

import pytest

from inaki.config.runtime_overrides import (
    AvisoPendiente,
    EntradaOverride,
    OverridesDeRuntime,
    destino_de,
)
from inaki.shared.errors import ConfigError


def _agente(agent_id: str, path: str, valor) -> EntradaOverride:
    return EntradaOverride.para(agent_id, path, valor)


class TestDestinoDerivadoDelSchema:
    @pytest.mark.parametrize(
        "path",
        ["llm.model", "tools.pinned", "memories.consolidation.enabled", "transcription"],
    )
    def test_un_bloque_que_el_agente_declara_va_al_agente(self, path):
        assert destino_de(path) == "agente"

    @pytest.mark.parametrize(
        "path",
        ["user.timezone", "scheduler.enabled", "app.log_level", "knowledge.sources"],
    )
    def test_un_bloque_solo_global_va_a_global(self, path):
        assert destino_de(path) == "global"

    def test_la_entrada_para_un_path_global_no_lleva_agent_id(self):
        entrada = EntradaOverride.para("anacleto", "user.timezone", "UTC")

        assert entrada.destino == "global"
        assert entrada.agent_id is None

    def test_la_entrada_para_un_path_de_agente_lleva_el_agent_id(self):
        entrada = EntradaOverride.para("anacleto", "llm.model", "x")

        assert entrada.destino == "agente"
        assert entrada.agent_id == "anacleto"


class TestValidacionDeLaEntrada:
    def test_destino_agente_sin_agent_id_es_un_error_de_programa(self):
        with pytest.raises(ValueError, match="agent_id"):
            EntradaOverride(destino="agente", agent_id=None, path="llm.model", valor="x")

    def test_destino_global_con_agent_id_es_un_error_de_programa(self):
        with pytest.raises(ValueError, match="agent_id"):
            EntradaOverride(destino="global", agent_id="a", path="user.timezone", valor="x")

    @pytest.mark.parametrize("path", ["", ".", "llm.", ".model", "llm..model"])
    def test_un_path_con_segmentos_vacios_se_rechaza(self, path):
        with pytest.raises(ValueError, match="path"):
            EntradaOverride(destino="global", agent_id=None, path=path, valor=1)

    def test_el_valor_se_copia_al_crear_la_entrada(self):
        valor = {"enabled": False}
        entrada = _agente("a", "memories.consolidation", valor)
        valor["enabled"] = True  # el llamador muta lo que pasó

        assert entrada.valor == {"enabled": False}


class TestCapasConElMotorUnicoDeMerge:
    def test_una_entrada_se_materializa_como_dict_anidado(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "llm.model", "gpt-5-mini"), None)

        assert store.capa_agente("a") == {"llm": {"model": "gpt-5-mini"}}

    def test_el_mismo_path_dos_veces_gana_la_ultima(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "llm.temperature", 0.3), None)
        store.agregar(_agente("a", "llm.temperature", 0.9), None)

        assert store.capa_agente("a") == {"llm": {"temperature": 0.9}}

    def test_dict_con_dict_se_funde(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "llm.model", "x"), None)
        store.agregar(_agente("a", "llm", {"temperature": 0.3}), None)

        assert store.capa_agente("a") == {"llm": {"model": "x", "temperature": 0.3}}

    def test_una_lista_reemplaza_entera(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "tools.pinned", ["delegate", "memory"]), None)
        store.agregar(_agente("a", "tools.pinned", ["scheduler"]), None)

        assert store.capa_agente("a") == {"tools": {"pinned": ["scheduler"]}}

    def test_none_pisa_el_bloque_entero(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "transcription", {"provider": "groq"}), None)
        store.agregar(_agente("a", "transcription", None), None)

        assert store.capa_agente("a") == {"transcription": None}

    def test_un_escalar_sobre_un_dict_es_config_error(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "llm.model", "x"), None)

        with pytest.raises(ConfigError, match="Conflicto de tipos"):
            store.agregar(_agente("a", "llm", "gpt"), None)

    def test_agregar_una_entrada_conflictiva_deja_el_store_intacto(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "llm.model", "x"), None)
        aviso = AvisoPendiente("a", "telegram", "1", "texto")

        with pytest.raises(ConfigError):
            store.agregar(_agente("a", "llm", "gpt"), aviso)

        assert store.capa_agente("a") == {"llm": {"model": "x"}}
        assert store.tomar_avisos() == []  # el aviso de la entrada rechazada tampoco entró

    def test_las_capas_global_y_por_agente_no_se_mezclan(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "llm.model", "ma"), None)
        store.agregar(_agente("b", "llm.model", "mb"), None)
        store.agregar(_agente("a", "user.timezone", "UTC"), None)

        assert store.capa_global() == {"user": {"timezone": "UTC"}}
        assert store.capas_por_agente() == {
            "a": {"llm": {"model": "ma"}},
            "b": {"llm": {"model": "mb"}},
        }
        assert store.capa_agente("b") == {"llm": {"model": "mb"}}

    def test_un_agente_sin_entradas_tiene_capa_vacia(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "llm.model", "ma"), None)

        assert store.capa_agente("otro") == {}  # hay entradas de OTRO agente, no de este
        assert store.capa_global() == {}


class TestConEsPura:
    def test_con_devuelve_un_candidato_sin_tocar_el_original(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "llm.model", "x"), None)

        candidato = store.con(_agente("a", "llm.temperature", 0.3))

        assert candidato.capa_agente("a") == {"llm": {"model": "x", "temperature": 0.3}}
        assert store.capa_agente("a") == {"llm": {"model": "x"}}

    def test_con_no_comparte_estado_con_el_original(self):
        store = OverridesDeRuntime()
        candidato = store.con(_agente("a", "llm.model", "x"))

        candidato.agregar(_agente("a", "llm.temperature", 0.3), None)

        assert store.capa_agente("a") == {}

    def test_con_no_arrastra_los_avisos_del_original(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "llm.model", "x"), AvisoPendiente("a", "telegram", "1", "t"))

        candidato = store.con(_agente("a", "llm.temperature", 0.3))

        assert candidato.tomar_avisos() == []
        assert len(store.tomar_avisos()) == 1


class TestCopiasDefensivas:
    def test_mutar_la_capa_devuelta_no_toca_el_store(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "memories.consolidation", {"enabled": False}), None)

        capa = store.capa_agente("a")
        capa["memories"]["consolidation"]["enabled"] = True

        assert store.capa_agente("a") == {"memories": {"consolidation": {"enabled": False}}}

    def test_el_loader_que_hace_pop_sobre_la_capa_no_vacia_el_store(self):
        # Regresión: load_global_config hace `knowledge_raw.pop("sources")` sobre el dict
        # mergeado, y deep_merge pone el valor del override POR REFERENCIA.
        fuentes = [{"id": "docs", "type": "document"}]
        store = OverridesDeRuntime()
        store.agregar(EntradaOverride.para("a", "knowledge", {"sources": fuentes}), None)

        store.capa_global()["knowledge"].pop("sources")

        assert store.capa_global() == {"knowledge": {"sources": fuentes}}

    def test_mutar_la_capa_global_no_toca_la_de_agentes_ni_viceversa(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "user.timezone", "UTC"), None)
        store.agregar(_agente("a", "llm.model", "x"), None)

        store.capas_por_agente()["a"]["llm"]["model"] = "pisado"
        store.capa_global()["user"]["timezone"] = "pisado"

        assert store.capa_agente("a") == {"llm": {"model": "x"}}
        assert store.capa_global() == {"user": {"timezone": "UTC"}}


class TestPathsRuntime:
    def test_son_las_hojas_pisadas_del_agente_mas_las_globales(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "llm.model", "x"), None)
        store.agregar(_agente("a", "user.timezone", "UTC"), None)

        assert store.paths_runtime("a") == frozenset({"llm.model", "user.timezone"})

    def test_un_bloque_se_expande_a_sus_hojas(self):
        store = OverridesDeRuntime()
        store.agregar(
            _agente("a", "memories.consolidation", {"enabled": False, "schedule": "0 4 * * *"}),
            None,
        )

        assert store.paths_runtime("a") == frozenset(
            {"memories.consolidation.enabled", "memories.consolidation.schedule"}
        )

    def test_una_lista_y_un_none_son_hojas(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "tools.pinned", ["delegate"]), None)
        store.agregar(_agente("a", "transcription", None), None)

        assert store.paths_runtime("a") == frozenset({"tools.pinned", "transcription"})

    def test_no_incluye_lo_pisado_para_otro_agente(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "llm.model", "ma"), None)
        store.agregar(_agente("b", "llm.temperature", 0.3), None)
        store.agregar(_agente("b", "user.timezone", "UTC"), None)

        assert store.paths_runtime("a") == frozenset({"llm.model", "user.timezone"})
        assert store.paths_runtime("b") == frozenset({"llm.temperature", "user.timezone"})

    def test_un_store_con_entradas_de_otro_agente_no_marca_nada_al_que_no_tiene(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "llm.model", "ma"), None)

        assert store.paths_runtime("sin-entradas") == frozenset()


class TestDescartarTodo:
    def test_devuelve_una_linea_por_entrada_en_orden_de_insercion(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("anacleto", "llm.model", "x"), None)
        store.agregar(_agente("anacleto", "user.timezone", "Europe/Madrid"), None)

        assert store.descartar_todo() == [
            "anacleto: llm.model = 'x'",
            "global: user.timezone = 'Europe/Madrid'",
        ]

    def test_vacia_entradas_y_avisos(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "llm.model", "x"), AvisoPendiente("a", "telegram", "1", "t"))
        store.agregar(_agente("a", "user.timezone", "UTC"), None)

        store.descartar_todo()

        assert store.capa_agente("a") == {}
        assert store.capa_global() == {}
        assert store.paths_runtime("a") == frozenset()
        assert store.tomar_avisos() == []

    def test_sin_entradas_devuelve_lista_vacia(self):
        assert OverridesDeRuntime().descartar_todo() == []

    def test_es_idempotente(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "llm.model", "x"), None)

        assert len(store.descartar_todo()) == 1
        assert store.descartar_todo() == []

    def test_el_store_sigue_usable_despues_de_descartar(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "llm.model", "x"), None)
        store.descartar_todo()

        store.agregar(_agente("a", "llm.model", "y"), None)

        assert store.capa_agente("a") == {"llm": {"model": "y"}}


class TestAvisosPendientes:
    def test_tomar_avisos_los_devuelve_y_los_vacia(self):
        store = OverridesDeRuntime()
        a1 = AvisoPendiente("a", "telegram", "1", "uno")
        a2 = AvisoPendiente("a", "telegram", "2", "dos")
        store.agregar(_agente("a", "llm.model", "x"), a1)
        store.agregar(_agente("a", "llm.temperature", 0.3), a2)

        assert store.tomar_avisos() == [a1, a2]
        assert store.tomar_avisos() == []

    def test_una_entrada_sin_aviso_no_registra_ninguno(self):
        store = OverridesDeRuntime()
        store.agregar(_agente("a", "llm.model", "x"), None)

        assert store.tomar_avisos() == []

    def test_el_aviso_lleva_el_chat_de_origen(self):
        store = OverridesDeRuntime()
        store.agregar(
            _agente("a", "llm.model", "x"), AvisoPendiente("a", "telegram", "123", "listo")
        )

        (aviso,) = store.tomar_avisos()

        assert (aviso.agent_id, aviso.canal, aviso.chat_id, aviso.texto) == (
            "a",
            "telegram",
            "123",
            "listo",
        )
