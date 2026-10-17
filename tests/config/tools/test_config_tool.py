"""Tests de ``ConfigTool`` — el acceso del agente a su propia config.

La tool es una capa de formato sobre ``RuntimeConfigUseCase``, así que acá se
prueba lo que es responsabilidad SUYA: que un fallo no se disfrace de éxito,
que un path que no existe devuelva por dónde seguir en vez de un "no" pelado, y
que un secreto venga con la instrucción explícita de no inventar su valor.
"""

from __future__ import annotations

import pytest

from inaki.config.tools.config_tool import _MAX_CAMPOS, ConfigTool
from inaki.config.use_cases.runtime_config import RuntimeConfigUseCase

_SECRETOS = frozenset({"providers.*.api_key"})


def _tool(memoria: dict, origenes: dict[str, str] | None = None) -> ConfigTool:
    return ConfigTool(
        runtime_config=RuntimeConfigUseCase(
            config_en_memoria=memoria,
            origenes=origenes,
            paths_secretos=_SECRETOS,
        )
    )


@pytest.fixture
def tool() -> ConfigTool:
    return _tool(
        {
            "llm": {"model": "gpt-4o", "temperature": 0.3},
            "providers": {"openai": {"api_key": "sk-secreto"}},
        },
        {"llm.model": "agent", "llm.temperature": "global"},
    )


async def test_get_devuelve_valor_y_capa_de_origen(tool: ConfigTool) -> None:
    res = await tool.execute(operation="get", path="llm.model")

    assert res.success
    assert "llm.model" in res.output
    assert "gpt-4o" in res.output
    assert "agent" in res.output


async def test_get_de_un_secreto_no_expone_el_valor_y_lo_dice(tool: ConfigTool) -> None:
    res = await tool.execute(operation="get", path="providers.openai.api_key")

    assert res.success
    assert "sk-secreto" not in res.output
    assert "credential" in res.output.lower()


async def test_get_de_un_path_inexistente_falla_y_nombra_vecinos(tool: ConfigTool) -> None:
    """Un "no existe" pelado empuja al modelo a inventar el valor que no encontró."""
    res = await tool.execute(operation="get", path="llm.modelo")

    assert not res.success
    assert "llm.model" in res.output


async def test_get_de_una_seccion_desconocida_remite_a_list(tool: ConfigTool) -> None:
    res = await tool.execute(operation="get", path="inventado.total")

    assert not res.success
    assert "list" in res.output


async def test_get_sin_path_falla(tool: ConfigTool) -> None:
    res = await tool.execute(operation="get")

    assert not res.success
    assert "path" in res.output


async def test_un_fallo_de_config_nunca_es_reintentable(tool: ConfigTool) -> None:
    """El snapshot está en memoria: reintentar devuelve exactamente lo mismo."""
    res = await tool.execute(operation="get", path="no.existe")

    assert not res.success
    assert res.retryable is False


async def test_list_con_prefijo_acota_al_subarbol(tool: ConfigTool) -> None:
    res = await tool.execute(operation="list", prefix="llm")

    assert res.success
    assert "llm.model" in res.output
    assert "providers" not in res.output


async def test_list_sin_prefijo_trae_todo(tool: ConfigTool) -> None:
    res = await tool.execute(operation="list")

    assert res.success
    assert "llm.model" in res.output
    assert "providers.openai.api_key" in res.output


async def test_list_nunca_filtra_un_secreto(tool: ConfigTool) -> None:
    res = await tool.execute(operation="list")

    assert "sk-secreto" not in res.output


async def test_list_de_un_prefijo_inexistente_falla(tool: ConfigTool) -> None:
    res = await tool.execute(operation="list", prefix="inventado")

    assert not res.success


async def test_operacion_desconocida_falla_nombrando_las_validas(tool: ConfigTool) -> None:
    res = await tool.execute(operation="set", path="llm.model")

    assert not res.success
    assert "get" in res.output and "list" in res.output


async def test_sin_operacion_falla(tool: ConfigTool) -> None:
    res = await tool.execute()

    assert not res.success


async def test_list_corta_al_techo_y_dice_como_acotar() -> None:
    """Volcar la config entera se come el presupuesto de tokens del turno."""
    memoria = {"bloque": {f"campo_{i:03d}": i for i in range(_MAX_CAMPOS + 10)}}
    res = await _tool(memoria).execute(operation="list")

    assert res.success
    assert "10 more" in res.output
    assert "prefix" in res.output


async def test_la_tool_no_ofrece_ninguna_operacion_de_escritura(tool: ConfigTool) -> None:
    """Cambiar config en caliente no existe: los valores se ligan al construir.

    Si alguien agrega un ``set`` acá sin resolver antes el rebind del runtime,
    la tool y el proceso empiezan a discrepar en silencio.
    """
    assert tool.parameters_schema["properties"]["operation"]["enum"] == ["get", "list"]


# ---------------------------------------------------------------------------
# Origen ``runtime`` (config-set-efimero)
# ---------------------------------------------------------------------------


def _tool_con_runtime() -> ConfigTool:
    return ConfigTool(
        runtime_config=RuntimeConfigUseCase(
            config_en_memoria={"llm": {"model": "gpt-5-mini", "temperature": 0.3}},
            origenes={"llm.model": "agent", "llm.temperature": "global"},
            paths_secretos=_SECRETOS,
            paths_runtime=frozenset({"llm.model"}),
        )
    )


async def test_get_de_un_valor_runtime_lo_marca_y_explica_como_se_pierde() -> None:
    res = await _tool_con_runtime().execute(operation="get", path="llm.model")

    assert res.success
    assert "gpt-5-mini" in res.output
    assert "[runtime]" in res.output
    assert "operation 'set'" in res.output
    assert "config_set" not in res.output
    assert "reload" in res.output.lower()


async def test_get_de_un_valor_de_disco_no_agrega_la_linea_de_runtime() -> None:
    res = await _tool_con_runtime().execute(operation="get", path="llm.temperature")

    assert res.success
    assert "[global]" in res.output
    assert "changed at runtime" not in res.output


async def test_list_marca_el_origen_runtime_solo_donde_corresponde() -> None:
    res = await _tool_con_runtime().execute(operation="list", prefix="llm")

    lineas = {linea.split(" = ")[0]: linea for linea in res.output.splitlines() if " = " in linea}
    assert lineas["llm.model"].endswith("[runtime]")
    assert lineas["llm.temperature"].endswith("[global]")


def test_la_description_de_la_tool_sin_escritura_es_de_solo_lectura() -> None:
    desc = _tool_con_runtime().description

    assert "read-only" in desc
    assert "[runtime]" in desc
    assert "config_set" not in desc
    # La frase vieja negaba el cambio en caliente: ya no es cierta.
    assert "cannot be changed from here" not in desc
