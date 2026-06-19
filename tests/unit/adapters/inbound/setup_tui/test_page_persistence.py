"""Tests de los hooks de persistencia de las páginas v3 (sin montar Textual).

Verifican que ``persist_field_saved`` / ``persist_tristate_saved`` / ``persist_add``
/ ``persist_delete`` de ``AgentDetailPage`` y ``GlobalPage`` construyen el dict
``cambios`` por path correcto y eligen la capa adecuada (principal vs secrets).
Patrón de ``test_base_page_helpers.py``: página con ``__new__`` + container mock.
"""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.domain.schema_node import AddableOption, SchemaNode
from adapters.inbound.setup_tui.screens.agent_detail_page import AgentDetailPage
from adapters.inbound.setup_tui.screens.global_page import GlobalPage
from core.ports.config_repository import LayerName
from core.use_cases.config._merge import CampoTriestado, TristadoValor


def _leaf(path: tuple[str, ...], kind: str = "scalar", value: object = "") -> SchemaNode:
    return SchemaNode(
        path=path, label=path[-1], is_section=False, field=Field(path[-1], value, kind)  # type: ignore[arg-type]
    )


def _agent_page() -> AgentDetailPage:
    page = AgentDetailPage.__new__(AgentDetailPage)
    page._container = MagicMock()
    page._agent_id = "anacleto"
    page._is_sub_agent = False
    return page


def _run(page, fn, *args):
    """Ejecuta ``fn`` con ``app`` y ``warn_on_invalid_refs`` parcheados."""
    app_mock = MagicMock()
    modulo = type(page).__module__
    with patch.object(type(page), "app", new_callable=PropertyMock, return_value=app_mock):
        with patch(f"{modulo}.warn_on_invalid_refs"):
            fn(*args)
    return app_mock


class TestAgentPersistField:
    def test_campo_normal_va_a_capa_agent(self):
        page = _agent_page()
        leaf = _leaf(("channels", "telegram", "groups", "behavior"), value="autonomous")
        _run(page, page.persist_field_saved, leaf, leaf.field)

        cambios, layer = _call(page._container.update_agent_layer.execute)
        assert cambios == {"channels": {"telegram": {"groups": {"behavior": "autonomous"}}}}
        assert layer == LayerName.AGENT

    def test_campo_secret_va_a_capa_secrets(self):
        page = _agent_page()
        leaf = _leaf(("channels", "telegram", "token"), kind="secret", value="TKN")
        _run(page, page.persist_field_saved, leaf, leaf.field)

        cambios, layer = _call(page._container.update_agent_layer.execute)
        assert cambios == {"channels": {"telegram": {"token": "TKN"}}}
        assert layer == LayerName.AGENT_SECRETS


class TestAgentPersistTristate:
    def test_inherit_emite_campo_triestado_inherit(self):
        page = _agent_page()
        leaf = _leaf(("memories", "llm", "provider"))
        result = MagicMock(mode="inherit", value=None)
        _run(page, page.persist_tristate_saved, leaf, leaf.field, result)

        cambios, layer = _call(page._container.update_agent_layer.execute)
        campo = cambios["memories"]["llm"]["provider"]
        assert isinstance(campo, CampoTriestado)
        assert campo.modo == TristadoValor.INHERIT
        assert layer == LayerName.AGENT

    def test_override_value_coerciona(self):
        page = _agent_page()
        leaf = _leaf(("memories", "llm", "max_tokens"))
        result = MagicMock(mode="override_value", value="2048")
        _run(page, page.persist_tristate_saved, leaf, leaf.field, result)

        cambios, _ = _call(page._container.update_agent_layer.execute)
        campo = cambios["memories"]["llm"]["max_tokens"]
        assert campo.modo == TristadoValor.OVERRIDE_VALOR
        assert campo.valor == 2048  # coercionado a int


class TestAgentPersistAdd:
    def test_anadir_seccion_crea_dict_vacio(self):
        page = _agent_page()
        parent = SchemaNode(path=("channels", "telegram"), label="telegram", is_section=True)
        opt = AddableOption("broadcast", "broadcast", is_section=True)
        _run(page, page.persist_add, parent, opt)

        cambios, layer = _call(page._container.update_agent_layer.execute)
        assert cambios == {"channels": {"telegram": {"broadcast": {}}}}
        assert layer == LayerName.AGENT

    def test_anadir_campo_usa_su_default(self):
        page = _agent_page()
        parent = SchemaNode(path=("channels", "telegram", "groups"), label="groups", is_section=True)
        opt = AddableOption("rate_limiter", "rate_limiter", is_section=False, default_value=5)
        _run(page, page.persist_add, parent, opt)

        cambios, _ = _call(page._container.update_agent_layer.execute)
        assert cambios == {"channels": {"telegram": {"groups": {"rate_limiter": 5}}}}

    def test_anadir_campo_secret_va_a_secrets(self):
        page = _agent_page()
        parent = SchemaNode(path=("channels", "telegram"), label="telegram", is_section=True)
        # is_secret=True lo deriva el builder del marcador del schema (token está
        # marcado). La capa se elige por ese flag, NO por el nombre del campo.
        opt = AddableOption("token", "token", is_section=False, default_value="", is_secret=True)
        _run(page, page.persist_add, parent, opt)

        _, layer = _call(page._container.update_agent_layer.execute)
        assert layer == LayerName.AGENT_SECRETS


class TestAgentPersistDelete:
    def test_solo_poda_en_la_capa_donde_existe(self):
        page = _agent_page()
        node = SchemaNode(path=("channels", "telegram", "groups"), label="groups", is_section=True)

        def _read(layer, agent_id=None):
            # groups existe solo en la capa principal, no en secrets.
            if layer == LayerName.AGENT:
                return {"channels": {"telegram": {"groups": {"behavior": "x"}}}}
            return {}

        page._container.repo.read_layer.side_effect = _read
        _run(page, page.persist_delete, node)

        # update se llamó UNA sola vez (capa AGENT), no en secrets.
        assert page._container.update_agent_layer.execute.call_count == 1
        cambios, layer = _call(page._container.update_agent_layer.execute)
        assert layer == LayerName.AGENT
        marcador = cambios["channels"]["telegram"]["groups"]
        assert isinstance(marcador, CampoTriestado)
        assert marcador.modo == TristadoValor.INHERIT


class TestGlobalPersist:
    def _global_page(self) -> GlobalPage:
        page = GlobalPage.__new__(GlobalPage)
        page._container = MagicMock()
        return page

    def test_campo_normal_va_a_global(self):
        page = self._global_page()
        leaf = _leaf(("llm", "model"), value="claude-x")
        _run(page, page.persist_field_saved, leaf, leaf.field)

        cambios, layer = _call(page._container.update_global_layer.execute, kw=True)
        assert cambios == {"llm": {"model": "claude-x"}}
        assert layer == LayerName.GLOBAL

    def test_campo_secret_va_a_global_secrets(self):
        page = self._global_page()
        leaf = _leaf(("admin", "auth_key"), kind="secret", value="k")
        _run(page, page.persist_field_saved, leaf, leaf.field)

        _, layer = _call(page._container.update_global_layer.execute, kw=True)
        assert layer == LayerName.GLOBAL_SECRETS


def _call(mock, kw: bool = False) -> tuple:
    """Extrae (cambios, layer) de la llamada al use case (kwargs o posicional)."""
    args, kwargs = mock.call_args
    if kw or "cambios" in kwargs:
        return kwargs.get("cambios", args[0] if args else None), kwargs.get("layer")
    # update_agent_layer.execute(agent_id=..., cambios=..., layer=...)
    return kwargs.get("cambios"), kwargs.get("layer")
