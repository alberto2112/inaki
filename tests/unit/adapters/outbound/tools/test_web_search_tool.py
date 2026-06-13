"""Tests para la tool web_search (Tavily, Tool Config Protocol)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
import yaml

from adapters.outbound.config_repository.yaml_tool_config_store import YamlToolConfigStore
from adapters.outbound.tools.web_search_tool import _TAVILY_ENDPOINT, WebSearchTool


def _make_tool(tmp_path: Path, initial: dict | None = None) -> WebSearchTool:
    # El store lee su propio tool_config.yaml; pre-sembramos escribiendo el archivo.
    if initial:
        (tmp_path / "tool_config.yaml").write_text(
            yaml.safe_dump({"tool_config": initial}), encoding="utf-8"
        )
    store = YamlToolConfigStore(
        store_path=tmp_path / "tool_config.yaml",
        key_path=tmp_path / "secret.key",
    )
    return WebSearchTool(config_store=store)


async def test_sin_api_key_instruye_configure_sin_red(tmp_path: Path):
    """Sin api_key el error instruye pedir la key al usuario y usar configure."""
    tool = _make_tool(tmp_path)

    result = await tool.execute(query="python")

    assert result.success is False
    assert "CONFIGURATION REQUIRED" in result.output
    assert "configure" in result.output
    assert "NO REINTENTES" in result.output


async def test_sin_query_devuelve_error(tmp_path: Path):
    tool = _make_tool(tmp_path, initial={"web_search": {"api_key": "tvly-test"}})

    result = await tool.execute()

    assert result.success is False
    assert "query" in result.output


async def test_configure_persiste_y_search_la_usa(tmp_path: Path):
    """Flujo completo del protocolo: configure desde el canal → search funciona."""
    tool = _make_tool(tmp_path)

    conf = await tool.execute(operation="configure", api_key="tvly-nueva", max_results=3)
    assert conf.success is True

    with respx.mock:
        route = respx.post(_TAVILY_ENDPOINT).mock(
            return_value=httpx.Response(200, json={"results": [], "answer": None})
        )
        await tool.execute(query="python")

    payload = json.loads(route.calls.last.request.content)
    assert payload["api_key"] == "tvly-nueva"
    assert payload["max_results"] == 3
    # y en disco quedó cifrada (en el archivo propio del store)
    contenido = (tmp_path / "tool_config.yaml").read_text(encoding="utf-8")
    assert "tvly-nueva" not in contenido
    assert "enc:" in contenido


async def test_configure_valida_search_depth(tmp_path: Path):
    tool = _make_tool(tmp_path)

    result = await tool.execute(operation="configure", search_depth="turbo")

    assert result.success is False
    assert "search_depth inválido" in result.output


async def test_show_config_enmascara_api_key(tmp_path: Path):
    tool = _make_tool(tmp_path)
    await tool.execute(operation="configure", api_key="tvly-secreta", search_depth="advanced")

    result = await tool.execute(operation="show_config")

    assert result.success is True
    assert "tvly-secreta" not in result.output
    assert "***" in result.output
    assert "advanced" in result.output


@respx.mock
async def test_search_exitoso_formatea_resultados(tmp_path: Path):
    respx.post(_TAVILY_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "answer": "Python es un lenguaje.",
                "results": [
                    {
                        "title": "Python.org",
                        "url": "https://python.org",
                        "content": "Sitio oficial",
                    }
                ],
            },
        )
    )
    tool = _make_tool(tmp_path, initial={"web_search": {"api_key": "tvly-test"}})

    result = await tool.execute(query="python")

    assert result.success is True
    assert "Resumen Tavily: Python es un lenguaje." in result.output
    assert "https://python.org" in result.output


@respx.mock
async def test_http_401_pide_reconfigurar(tmp_path: Path):
    respx.post(_TAVILY_ENDPOINT).mock(return_value=httpx.Response(401))
    tool = _make_tool(tmp_path, initial={"web_search": {"api_key": "tvly-mala"}})

    result = await tool.execute(query="python")

    assert result.success is False
    assert "401" in result.output
    assert "configure" in result.output
