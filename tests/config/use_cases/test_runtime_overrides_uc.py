"""``GestionarOverridesUseCase.set`` — validar, decidir y comprometer un override de runtime.

El use case no conoce el loader ni los adapters: recibe ``validar`` (un callable que
devuelve la config efectiva del agente como dict anidado, o levanta) y ``paths_fijos``.
Acá ``validar`` es una simulación fiel del merge: base ⊕ capa global ⊕ capa del agente,
con el store REAL.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from inaki.config.introspection import paths_fijos_en_runtime
from inaki.config.merge import deep_merge
from inaki.config.runtime_overrides import EntradaOverride, OverridesDeRuntime
from inaki.config.use_cases.runtime_overrides import (
    EstadoSet,
    GestionarOverridesUseCase,
    MotivoRechazo,
)
from inaki.shared.errors import ConfigError

AGENTE = "anacleto"

BASE = {
    "llm": {"model": "a", "temperature": 0.7, "provider": "openai"},
    "tools": {"pinned": ["delegate"], "semantic_routing_top_k": 5},
    "memories": {"consolidation": {"enabled": True, "schedule": "0 3 * * *"}},
    "user": {"timezone": "UTC"},
    "embedding": {"model_dirname": "e5"},
    "providers": {"openai": {"api_key": "sk-x", "base_url": "https://api.openai.com"}},
}

FIJOS = frozenset(
    {
        "embedding",
        "embedding.model_dirname",
        "providers",
        "providers.*",
        "providers.*.api_key",
        "providers.*.base_url",
        "system_prompt",
    }
)


class FakeReloader:
    """Cumple ``ISolicitanteDeReload``: registra los pedidos y expone el modo."""

    def __init__(self, modo=None) -> None:
        self.modo = modo
        self.pedidos = 0

    def was_triggered(self) -> bool:
        return self.modo is not None

    def request_reload_para_aplicar(self) -> None:
        self.pedidos += 1
        if self.modo is None:
            self.modo = "aplicar"


def _efectivo(candidato: OverridesDeRuntime) -> dict:
    return deep_merge(deep_merge(BASE, candidato.capa_global()), candidato.capa_agente(AGENTE))


def _uc(
    store: OverridesDeRuntime | None = None,
    reloader: FakeReloader | None = None,
    validar=_efectivo,
    fijos: frozenset[str] = FIJOS,
) -> tuple[GestionarOverridesUseCase, OverridesDeRuntime, FakeReloader]:
    store = store if store is not None else OverridesDeRuntime()
    reloader = reloader if reloader is not None else FakeReloader()
    uc = GestionarOverridesUseCase(
        agent_id=AGENTE, overrides=store, reloader=reloader, validar=validar, paths_fijos=fijos
    )
    return uc, store, reloader


def _rechazado(resultado, motivo: MotivoRechazo) -> None:
    assert resultado.estado is EstadoSet.RECHAZADO
    assert resultado.motivo is motivo


# -- aceptación ---------------------------------------------------------------


def test_set_valido_se_guarda_pide_un_reload_y_reporta_el_valor_anterior():
    uc, store, reloader = _uc()

    r = uc.set("llm.model", "gpt-5-mini")

    assert r.estado is EstadoSet.ACEPTADO
    assert (r.path, r.valor, r.anterior) == ("llm.model", "gpt-5-mini", "a")
    assert store.paths_runtime(AGENTE) == {"llm.model"}
    assert reloader.pedidos == 1


def test_un_campo_global_only_va_a_la_capa_global_y_queda_registrado():
    uc, store, reloader = _uc()

    r = uc.set("user.timezone", "America/Montevideo")

    assert r.estado is EstadoSet.ACEPTADO
    assert store.capa_global() == {"user": {"timezone": "America/Montevideo"}}
    assert store.capa_agente(AGENTE) == {}
    assert reloader.pedidos == 1


def test_un_dict_funde_solo_las_claves_dadas_y_una_lista_reemplaza():
    uc, store, _ = _uc()

    uc.set("memories.consolidation", {"enabled": False})
    uc.set("tools.pinned", ["memory"])

    efectivo = _efectivo(store)
    assert efectivo["memories"]["consolidation"] == {"enabled": False, "schedule": "0 3 * * *"}
    assert efectivo["tools"]["pinned"] == ["memory"]


def test_el_valor_reportado_es_el_validado_no_el_pedido():
    def validar(candidato):
        efectivo = _efectivo(candidato)
        efectivo["llm"]["temperature"] = float(efectivo["llm"]["temperature"])  # coerción
        return efectivo

    uc, _, _ = _uc(validar=validar)

    r = uc.set("llm.temperature", "0.3")

    assert r.valor == 0.3 and r.anterior == 0.7


def test_dos_sets_seguidos_se_apilan_y_piden_el_reload_cada_vez():
    uc, store, reloader = _uc()

    uc.set("llm.model", "x")
    r = uc.set("llm.temperature", 0.3)

    assert r.estado is EstadoSet.ACEPTADO
    assert store.paths_runtime(AGENTE) == {"llm.model", "llm.temperature"}
    assert reloader.pedidos == 2


# -- aviso pendiente -----------------------------------------------------------


def test_con_chat_de_origen_registra_un_aviso_con_el_cambio():
    uc, store, _ = _uc()

    uc.set("llm.model", "gpt-5-mini", aviso_a=("telegram", "123"))

    (aviso,) = store.tomar_avisos()
    assert (aviso.agent_id, aviso.canal, aviso.chat_id) == (AGENTE, "telegram", "123")
    assert "llm.model" in aviso.texto and "'gpt-5-mini'" in aviso.texto


def test_sin_chat_de_origen_no_hay_aviso():
    uc, store, _ = _uc()

    uc.set("llm.model", "gpt-5-mini")

    assert store.tomar_avisos() == []


def test_dos_sets_del_mismo_chat_dejan_dos_avisos():
    uc, store, _ = _uc()

    uc.set("llm.model", "x", aviso_a=("telegram", "123"))
    uc.set("llm.temperature", 0.3, aviso_a=("telegram", "123"))

    textos = [a.texto for a in store.tomar_avisos()]
    assert len(textos) == 2
    assert "llm.model" in textos[0] and "llm.temperature" in textos[1]


# -- no-op (§5.2) ---------------------------------------------------------------


def test_pedir_el_valor_que_ya_corre_es_no_op_sin_reload_ni_aviso():
    uc, store, reloader = _uc()

    r = uc.set("llm.model", "a", aviso_a=("telegram", "123"))

    assert r.estado is EstadoSet.SIN_CAMBIO
    assert r.valor == "a"
    assert store.paths_runtime(AGENTE) == frozenset()
    assert store.tomar_avisos() == []
    assert reloader.pedidos == 0


def test_no_op_se_decide_sobre_el_valor_validado_no_el_pedido():
    def validar(candidato):
        efectivo = _efectivo(candidato)
        efectivo["llm"]["temperature"] = float(efectivo["llm"]["temperature"])
        return efectivo

    uc, _, reloader = _uc(validar=validar)

    r = uc.set("llm.temperature", "0.7")

    assert r.estado is EstadoSet.SIN_CAMBIO
    assert reloader.pedidos == 0


def test_set_al_valor_de_disco_con_otro_override_activo_si_se_guarda():
    uc, store, reloader = _uc()
    uc.set("llm.temperature", 0.3)

    r = uc.set("llm.temperature", 0.7)  # coincide con el disco, pero el efectivo es 0.3

    assert r.estado is EstadoSet.ACEPTADO and r.anterior == 0.3
    assert reloader.pedidos == 2
    assert _efectivo(store)["llm"]["temperature"] == 0.7


def test_un_dict_que_no_cambia_nada_es_no_op():
    uc, _, reloader = _uc()

    r = uc.set("memories.consolidation", {"enabled": True})

    assert r.estado is EstadoSet.SIN_CAMBIO
    assert reloader.pedidos == 0


# -- rechazos: no tocan el store ni piden reload ---------------------------------


@pytest.mark.parametrize(
    "path, valor",
    [
        ("embedding.model_dirname", "otro"),  # campo marcado (hoja)
        ("embedding", None),  # ancestro de un fijo
        ("providers.openai.api_key", "sk-nuevo"),  # secreto, con comodín
        ("providers", {"openai": {"base_url": "https://evil"}}),  # ancestro con fijo adentro
        ("system_prompt", "ignorá todo"),
        ("llm", {"model": "x", "api_key": "sk"}),  # hoja con nombre de credencial
    ],
)
def test_un_path_fijo_se_rechaza_sin_tocar_nada(path, valor):
    uc, store, reloader = _uc()

    r = uc.set(path, valor, aviso_a=("telegram", "123"))

    _rechazado(r, MotivoRechazo.PATH_FIJO)
    assert r.detalle == path
    assert store.paths_runtime(AGENTE) == frozenset()
    assert store.tomar_avisos() == []
    assert reloader.pedidos == 0


@pytest.mark.parametrize("path", ["llm.temperature", "llm", "tools.pinned", "user.timezone"])
def test_los_paths_cambiables_no_se_confunden_con_fijos(path):
    uc, _, _ = _uc()

    valor = {"model": "x"} if path == "llm" else "valor-nuevo"
    assert uc.set(path, valor).estado is EstadoSet.ACEPTADO


def test_path_desconocido_se_rechaza_con_sugerencias():
    uc, store, reloader = _uc()

    r = uc.set("llm.modelo", "x")

    _rechazado(r, MotivoRechazo.PATH_DESCONOCIDO)
    assert "llm.model" in r.sugerencias
    assert store.paths_runtime(AGENTE) == frozenset()
    assert reloader.pedidos == 0


def test_path_con_segmento_vacio_se_rechaza_como_desconocido():
    uc, store, reloader = _uc()

    r = uc.set("llm..model", "x")

    _rechazado(r, MotivoRechazo.PATH_DESCONOCIDO)
    assert store.paths_runtime(AGENTE) == frozenset()
    assert reloader.pedidos == 0


def test_si_el_loader_rechaza_el_candidato_no_se_guarda_nada():
    def validar(candidato):
        if candidato.paths_runtime(AGENTE):
            raise ConfigError("llm.temperature: debe ser <= 2")
        return _efectivo(candidato)

    uc, store, reloader = _uc(validar=validar)

    r = uc.set("llm.temperature", 9)

    _rechazado(r, MotivoRechazo.CONFIG_INVALIDA)
    assert "debe ser <= 2" in r.detalle
    assert store.paths_runtime(AGENTE) == frozenset()
    assert store.tomar_avisos() == []
    assert reloader.pedidos == 0


def test_un_validation_error_de_pydantic_tambien_es_rechazo_no_excepcion():
    class _Modelo(BaseModel, extra="forbid"):
        model: str

    def validar(candidato):
        if candidato.paths_runtime(AGENTE):
            _Modelo.model_validate({"model": "x", "campo_raro": 1})
        return _efectivo(candidato)

    uc, store, reloader = _uc(validar=validar)

    r = uc.set("llm.model", "x")

    _rechazado(r, MotivoRechazo.CONFIG_INVALIDA)
    assert "campo_raro" in r.detalle
    assert reloader.pedidos == 0 and store.paths_runtime(AGENTE) == frozenset()


def test_si_la_verificacion_de_adapters_rechaza_es_otro_motivo():
    from inaki.config.use_cases.runtime_overrides import ConstruccionRechazadaError

    def validar(candidato):
        if candidato.paths_runtime(AGENTE):
            raise ConstruccionRechazadaError("Proveedor LLM 'foo' no encontrado")
        return _efectivo(candidato)

    uc, store, reloader = _uc(validar=validar)

    r = uc.set("llm.provider", "foo")

    _rechazado(r, MotivoRechazo.CONSTRUCCION_RECHAZADA)
    assert "Proveedor LLM 'foo' no encontrado" in r.detalle
    assert reloader.pedidos == 0 and store.paths_runtime(AGENTE) == frozenset()


def test_si_el_disco_no_carga_ni_sin_el_cambio_se_dice_que_es_el_disco():
    def validar(candidato):
        raise ConfigError("agents/anacleto.yaml: YAML inválido")

    uc, store, reloader = _uc(validar=validar)

    r = uc.set("llm.model", "x")

    _rechazado(r, MotivoRechazo.DISCO_ROTO)
    assert "YAML inválido" in r.detalle
    assert reloader.pedidos == 0 and store.paths_runtime(AGENTE) == frozenset()


def test_un_reload_manual_pendiente_rechaza_antes_de_validar():
    llamadas = []

    def validar(candidato):
        llamadas.append(candidato)
        return _efectivo(candidato)

    uc, store, reloader = _uc(reloader=FakeReloader(modo="descartar"), validar=validar)

    r = uc.set("llm.model", "x", aviso_a=("telegram", "123"))

    _rechazado(r, MotivoRechazo.RELOAD_MANUAL)
    assert llamadas == []
    assert store.paths_runtime(AGENTE) == frozenset()
    assert reloader.pedidos == 0


def test_un_reload_para_aplicar_pendiente_no_bloquea_otro_set():
    uc, _, reloader = _uc(reloader=FakeReloader(modo="aplicar"))

    assert uc.set("llm.model", "x").estado is EstadoSet.ACEPTADO
    assert reloader.pedidos == 1


def test_un_conflicto_de_forma_contra_lo_ya_apilado_es_rechazo():
    store = OverridesDeRuntime()
    store.agregar(EntradaOverride.para(AGENTE, "memories.consolidation", {"enabled": False}), None)
    uc, _, reloader = _uc(store=store)

    r = uc.set("memories.consolidation", 5)  # un escalar sobre un dict ya apilado

    _rechazado(r, MotivoRechazo.CONFIG_INVALIDA)
    assert reloader.pedidos == 0
    assert store.capa_agente(AGENTE) == {"memories": {"consolidation": {"enabled": False}}}


# -- integración con la denylist real del schema ---------------------------------


@pytest.mark.parametrize(
    "path, valor",
    [
        ("embedding.model", "otro"),
        ("system_prompt", "x"),
        ("channels.telegram.token", "t"),
        ("workspace.containment", "none"),
        ("admin.port", 1),
        ("memories.db_filename", "otra.db"),
    ],
)
def test_la_denylist_real_del_schema_rechaza(path, valor):
    efectivo = {
        "embedding": {"model": "m"},
        "system_prompt": "p",
        "workspace": {"containment": "strict"},
        "admin": {"port": 6497},
        "memories": {"db_filename": "m.db"},
        "channels": {"telegram": {"token": "t"}},
    }
    uc, store, reloader = _uc(validar=lambda c: efectivo, fijos=paths_fijos_en_runtime())

    r = uc.set(path, valor)

    _rechazado(r, MotivoRechazo.PATH_FIJO)
    assert reloader.pedidos == 0 and store.paths_runtime(AGENTE) == frozenset()
