"""Tests de ``resolve_choices`` — el resolver de choices dinámicos por ruta.

Verifica que las rutas de provider se resuelvan a la UNIÓN de providers globales
+ locales, que las rutas de ``agent_id`` se resuelvan a sub-agentes, y que el
resolver tolere un repo que falla sin romper el render del árbol.
"""

from __future__ import annotations

from typing import Any

from adapters.inbound.setup_tui.choices import resolve_choices
from core.ports.config_repository import LayerName


class _FakeRepo:
    """Repo mínimo: devuelve providers globales en GLOBAL y una lista de sub-agentes."""

    def __init__(self, global_providers: list[str], subagents: list[str]) -> None:
        self._gp = global_providers
        self._sa = subagents

    def read_layer(self, layer: LayerName, agent_id: str | None = None) -> dict[str, Any]:
        if layer == LayerName.GLOBAL:
            return {"providers": {k: {} for k in self._gp}}
        return {}

    def list_sub_agents(self) -> list[str]:
        return list(self._sa)


def test_provider_paths_unen_globales_y_locales_ordenados():
    repo = _FakeRepo(global_providers=["openai", "groq"], subagents=["extractor"])
    datos = {"providers": {"openai-work": {}}}  # provider local del scope (agente)

    out = resolve_choices(repo, datos)

    esperado = ("groq", "openai", "openai-work")  # unión, ordenado
    assert out["llm.provider"] == esperado
    assert out["embedding.provider"] == esperado
    assert out["transcription.provider"] == esperado


def test_agent_id_paths_son_subagentes():
    repo = _FakeRepo(global_providers=[], subagents=["memory_extractor", "memory_reconciler"])

    out = resolve_choices(repo, {})

    assert out["memories.consolidation.agent_id"] == ("memory_extractor", "memory_reconciler")
    assert out["memories.reconciliation.agent_id"] == ("memory_extractor", "memory_reconciler")


def test_tolera_repo_que_falla():
    """Si el repo revienta, cada fuente queda vacía en vez de romper el árbol."""

    class _Boom:
        def read_layer(self, *a: Any, **k: Any) -> dict[str, Any]:
            raise RuntimeError("read falló")

        def list_sub_agents(self) -> list[str]:
            raise RuntimeError("list falló")

    out = resolve_choices(_Boom(), {})

    assert out["llm.provider"] == ()
    assert out["memories.consolidation.agent_id"] == ()
