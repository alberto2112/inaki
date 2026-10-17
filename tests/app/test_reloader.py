"""``DaemonReloader`` con modos: el reload manual descarta, el de ``config_set`` aplica.

El ``/reload`` manual (las tres puertas: REST, CLI, Telegram) descarta los overrides
de runtime en el momento del pedido y devuelve qué descartó. El reload que dispara
``config_set`` (``request_reload_para_aplicar``) los conserva: el bootstrap los lee
del store. Si ambos coinciden, gana el manual.
"""

from __future__ import annotations

from inaki.app.reloader import DaemonReloader
from inaki.config.ports import ISolicitanteDeReload
from inaki.config.runtime_overrides import AvisoPendiente, EntradaOverride, OverridesDeRuntime


def _store_con_cambios() -> OverridesDeRuntime:
    store = OverridesDeRuntime()
    store.agregar(
        EntradaOverride.para("anacleto", "llm.model", "gpt-5-mini"),
        AvisoPendiente("anacleto", "telegram", "123", "Volví"),
    )
    store.agregar(EntradaOverride.para("anacleto", "llm.temperature", 0.3), None)
    return store


def test_estado_inicial_sin_pedido():
    reloader = DaemonReloader()

    assert reloader.was_triggered() is False
    assert reloader.modo is None


def test_request_reload_sin_store_devuelve_lista_vacia_y_dispara():
    reloader = DaemonReloader()

    assert reloader.request_reload() == []
    assert reloader.was_triggered() is True
    assert reloader.modo == "descartar"


def test_request_reload_manual_descarta_y_devuelve_las_lineas():
    store = _store_con_cambios()
    reloader = DaemonReloader(store)

    descartados = reloader.request_reload()

    assert descartados == [
        "anacleto: llm.model = 'gpt-5-mini'",
        "anacleto: llm.temperature = 0.3",
    ]
    # El store quedó vacío: ni capas ni avisos sobreviven al reload manual.
    assert store.capas_por_agente() == {}
    assert store.tomar_avisos() == []
    assert reloader.modo == "descartar"
    assert reloader.was_triggered() is True


def test_request_reload_para_aplicar_conserva_el_store():
    store = _store_con_cambios()
    reloader = DaemonReloader(store)

    reloader.request_reload_para_aplicar()

    assert reloader.was_triggered() is True
    assert reloader.modo == "aplicar"
    assert store.capa_agente("anacleto") == {"llm": {"model": "gpt-5-mini", "temperature": 0.3}}
    assert len(store.tomar_avisos()) == 1


def test_para_aplicar_es_idempotente():
    reloader = DaemonReloader(_store_con_cambios())

    reloader.request_reload_para_aplicar()
    reloader.request_reload_para_aplicar()

    assert reloader.modo == "aplicar"


def test_el_manual_gana_sobre_un_para_aplicar_previo():
    store = _store_con_cambios()
    reloader = DaemonReloader(store)
    reloader.request_reload_para_aplicar()

    descartados = reloader.request_reload()

    assert reloader.modo == "descartar"
    assert len(descartados) == 2
    assert store.capas_por_agente() == {}


def test_para_aplicar_no_pisa_un_manual_previo():
    store = _store_con_cambios()
    reloader = DaemonReloader(store)
    reloader.request_reload()

    reloader.request_reload_para_aplicar()

    assert reloader.modo == "descartar"


async def test_wait_for_reload_se_libera_con_cualquiera_de_los_dos():
    for disparar in ("request_reload", "request_reload_para_aplicar"):
        reloader = DaemonReloader(OverridesDeRuntime())
        getattr(reloader, disparar)()
        await reloader.wait_for_reload()  # no cuelga
        assert reloader.was_triggered() is True


def test_cumple_el_protocol_que_consume_config():
    """El use case de config lee ``modo`` / ``was_triggered`` / ``request_reload_para_aplicar``
    por un Protocol propio: ``config`` no importa ``app``."""
    assert isinstance(DaemonReloader(), ISolicitanteDeReload)
