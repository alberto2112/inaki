"""Privilegio POR OPERACIÓN: ``unlocked_operations`` / ``operation_param`` / ``operation_of``.

Una tool ``requires_operator`` con ``unlocked_operations`` vacía queda entera protegida
(el comportamiento histórico). Con operaciones desbloqueadas, solo esas corren sin
operador; el resto sigue protegido. Despacho y autorización salen de la MISMA función
(``operation_of``), y lo ausente falla cerrado. El registry valida la declaración al
REGISTRAR, no al ejecutar.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from inaki.kernel.ports.tool_port import ITool, IToolExecutor, ToolResult
from inaki.tools.registry import ToolRegistry

_SCHEMA = {
    "type": "object",
    "properties": {"operation": {"type": "string", "enum": ["get", "list", "set"]}},
}


def _tool(
    nombre: str = "mixta",
    *,
    requires_operator: bool = True,
    unlocked: frozenset[str] = frozenset({"get", "list"}),
    param: str | None = "operation",
    schema: dict | None = None,
) -> ITool:
    clase = type(
        "_Tool",
        (ITool,),
        {
            "name": nombre,
            "description": "tool de prueba",
            "parameters_schema": _SCHEMA if schema is None else schema,
            "requires_operator": requires_operator,
            "unlocked_operations": unlocked,
            "operation_param": param,
            "execute": lambda self, **kw: ToolResult(tool_name=nombre, output="ok", success=True),
        },
    )
    return clase()


def _registry(*tools: ITool) -> ToolRegistry:
    registry = ToolRegistry(AsyncMock())
    for tool in tools:
        registry.register(tool)
    return registry


# -- defaults de ITool ---------------------------------------------------------------


def test_defaults_de_itool_no_desbloquean_nada():
    assert ITool.unlocked_operations == frozenset()
    assert ITool.operation_param is None


# -- operation_of -------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, esperada",
    [
        ({"operation": "get"}, "get"),
        ({"operation": "  SET "}, "set"),  # normaliza: mismo criterio que el despacho
        ({"operation": ""}, None),  # vacía = ausente
        ({"operation": "   "}, None),
        ({"operation": None}, None),
        ({}, None),
    ],
)
def test_operation_of_normaliza_y_devuelve_none_si_falta(kwargs, esperada):
    assert _tool().operation_of(kwargs) == esperada


def test_operation_of_sin_operation_param_devuelve_none_aunque_haya_valor():
    assert _tool(unlocked=frozenset(), param=None).operation_of({"operation": "get"}) is None


def test_operation_of_respeta_un_parametro_con_otro_nombre():
    tool = _tool(
        param="action",
        schema={"type": "object", "properties": {"action": {"enum": ["get", "list", "set"]}}},
    )
    assert tool.operation_of({"action": "List"}) == "list"
    assert tool.operation_of({"operation": "list"}) is None


# -- IToolExecutor.requires_operator(tool_name, kwargs) ------------------------------


def test_operacion_desbloqueada_no_requiere_operador():
    registry = _registry(_tool())
    assert registry.requires_operator("mixta", {"operation": "get"}) is False
    assert registry.requires_operator("mixta", {"operation": "list"}) is False


def test_operacion_no_desbloqueada_requiere_operador():
    assert _registry(_tool()).requires_operator("mixta", {"operation": "set"}) is True


@pytest.mark.parametrize("kwargs", [{}, {"operation": ""}, {"operation": None}, {"otro": "get"}])
def test_operacion_ausente_falla_cerrado(kwargs):
    assert _registry(_tool()).requires_operator("mixta", kwargs) is True


def test_la_normalizacion_es_la_del_despacho():
    """``GET`` con mayúsculas y espacios es la operación ``get``: la misma que va a despachar."""
    assert _registry(_tool()).requires_operator("mixta", {"operation": " GET "}) is False


def test_tool_privilegiada_sin_desbloqueadas_sigue_entera_protegida():
    registry = _registry(_tool("entera", unlocked=frozenset(), param=None))
    assert registry.requires_operator("entera", {}) is True
    assert registry.requires_operator("entera", {"operation": "get"}) is True


def test_tool_comun_y_tool_inexistente_no_requieren_operador():
    comun = _tool("comun", requires_operator=False, unlocked=frozenset(), param=None)
    registry = _registry(comun)
    assert registry.requires_operator("comun", {"operation": "set"}) is False
    assert registry.requires_operator("no_existe", {"operation": "set"}) is False


def test_executor_expone_operation_of_con_la_normalizacion_de_la_tool():
    registry = _registry(_tool())
    assert registry.operation_of("mixta", {"operation": " Set"}) == "set"
    assert registry.operation_of("mixta", {}) is None
    assert registry.operation_of("no_existe", {"operation": "set"}) is None


def test_el_default_de_operation_of_en_el_port_es_none():
    """No es autorización (eso es ``requires_operator``, abstracto): solo redacta el rechazo."""

    class _Minimo(IToolExecutor):
        def register(self, tool): ...
        async def execute(self, tool_name, **kwargs): ...
        def requires_operator(self, tool_name, kwargs): ...
        def get_schemas(self): ...
        async def get_schemas_relevant(self, query_embedding, top_k=5, min_score=0.0): ...
        async def get_schemas_relevant_with_scores(
            self, query_embedding, top_k=5, min_score=0.0
        ): ...

    assert _Minimo().operation_of("x", {"operation": "set"}) is None


# -- validación ruidosa al registrar -------------------------------------------------


def test_unlocked_con_requires_operator_false_es_una_contradiccion():
    tool = _tool("contradictoria", requires_operator=False)
    with pytest.raises(ValueError, match="contradictoria") as exc:
        _registry(tool)
    assert "requires_operator" in str(exc.value)


def test_unlocked_sin_operation_param_se_rechaza():
    tool = _tool("sin_param", param=None)
    with pytest.raises(ValueError, match="sin_param") as exc:
        _registry(tool)
    assert "operation_param" in str(exc.value)


def test_unlocked_fuera_del_enum_se_rechaza_y_nombra_la_operacion():
    tool = _tool("typo", unlocked=frozenset({"get", "lisst"}))
    with pytest.raises(ValueError, match="typo") as exc:
        _registry(tool)
    assert "lisst" in str(exc.value)


def test_unlocked_con_param_que_no_esta_en_el_schema_se_rechaza():
    tool = _tool("sin_enum", schema={"type": "object", "properties": {}})
    with pytest.raises(ValueError, match="sin_enum"):
        _registry(tool)


def test_unlocked_con_param_sin_enum_se_rechaza():
    tool = _tool("param_libre", schema={"type": "object", "properties": {"operation": {}}})
    with pytest.raises(ValueError, match="param_libre"):
        _registry(tool)


def test_el_rechazo_no_deja_la_tool_registrada():
    registry = ToolRegistry(AsyncMock())
    with pytest.raises(ValueError):
        registry.register(_tool("fallida", requires_operator=False))
    assert "fallida" not in registry


def test_declaracion_valida_se_registra():
    registry = _registry(_tool("valida"))
    assert "valida" in registry


# -- pisar una tool: perder privilegio por operación ----------------------------------


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]


def test_pisar_con_una_que_desbloquea_mas_operaciones_loguea_warning(caplog):
    registry = _registry(_tool("t", unlocked=frozenset({"get"})))

    with caplog.at_level(logging.WARNING, logger="inaki.tools.registry"):
        registry.register(_tool("t", unlocked=frozenset({"get", "set"})))

    avisos = _warnings(caplog)
    assert len(avisos) == 1 and "t" in avisos[0] and "set" in avisos[0]
    assert registry.requires_operator("t", {"operation": "set"}) is False


def test_pisar_una_entera_protegida_con_una_parcial_loguea_warning(caplog):
    registry = _registry(_tool("t", unlocked=frozenset(), param=None))

    with caplog.at_level(logging.WARNING, logger="inaki.tools.registry"):
        registry.register(_tool("t", unlocked=frozenset({"list"})))

    avisos = _warnings(caplog)
    assert len(avisos) == 1 and "list" in avisos[0]


def test_pisar_una_parcial_con_una_comun_sigue_logueando_warning(caplog):
    registry = _registry(_tool("t"))

    with caplog.at_level(logging.WARNING, logger="inaki.tools.registry"):
        registry.register(_tool("t", requires_operator=False, unlocked=frozenset(), param=None))

    assert len(_warnings(caplog)) == 1


@pytest.mark.parametrize(
    "antes, despues",
    [
        (frozenset({"get", "list"}), frozenset({"get", "list"})),  # igual
        (frozenset({"get", "list"}), frozenset({"get"})),  # más estricta
        (frozenset({"get"}), frozenset()),  # entera protegida: más estricta
    ],
)
def test_pisar_con_igual_o_mas_proteccion_no_loguea(caplog, antes, despues):
    registry = _registry(_tool("t", unlocked=antes, param="operation" if antes else None))

    with caplog.at_level(logging.WARNING, logger="inaki.tools.registry"):
        registry.register(_tool("t", unlocked=despues, param="operation" if despues else None))

    assert _warnings(caplog) == []
