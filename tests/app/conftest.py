"""Fakes y builders de config para probar el ensamblador sin IO externo.

Los bordes externos (LLM, embedding) se sustituyen parcheando la clase en el
``wiring.py`` de su módulo, igual que en el camino dorado; todo lo demás es real
(SQLite en el home temporal, registros, use cases).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from inaki.config import (
    AgentConfig,
    AgentDelegationConfig,
    AppConfig,
    ChatHistoryConfig,
    DelegationConfig,
    EmbeddingConfig,
    GlobalConfig,
    LLMConfig,
    MemoriesConfig,
    ProviderConfig,
    SchedulerConfig,
    SkillsConfig,
    ToolsConfig,
    WorkspaceConfig,
)
from inaki.config.home import set_inaki_home


class FakeEmbedder:
    async def embed_passage(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


def agent_cfg(
    agent_id: str = "test-agent",
    *,
    delegation_enabled: bool = False,
    allowed_targets: list[str] | None = None,
    channels: dict | None = None,
    **extra: object,
) -> AgentConfig:
    return AgentConfig(
        id=agent_id,
        name=agent_id.capitalize(),
        description=f"Agent {agent_id}",
        system_prompt="Test prompt",
        llm=LLMConfig(provider="openrouter", model="test-model"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_dirname="models/test"),
        memories=MemoriesConfig(db_filename=":memory:"),
        chat_history=ChatHistoryConfig(db_filename="data/history.db"),
        delegation=AgentDelegationConfig(
            enabled=delegation_enabled, allowed_targets=allowed_targets or []
        ),
        providers={"openrouter": ProviderConfig(api_key="test-key")},
        channels=channels or {},
        **extra,  # type: ignore[arg-type]
    )


def global_cfg(*, max_iterations_per_sub: int = 10, timeout_seconds: int = 60) -> GlobalConfig:
    return GlobalConfig(
        app=AppConfig(ext_dirs=[]),
        llm=LLMConfig(provider="openrouter", model="test-model"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_dirname="models/test"),
        memories=MemoriesConfig(db_filename=":memory:"),
        chat_history=ChatHistoryConfig(db_filename="data/history.db"),
        skills=SkillsConfig(),
        tools=ToolsConfig(),
        scheduler=SchedulerConfig(),
        workspace=WorkspaceConfig(),
        delegation=DelegationConfig(
            max_iterations_per_sub=max_iterations_per_sub, timeout_seconds=timeout_seconds
        ),
        providers={"openrouter": ProviderConfig(api_key="test-key")},
    )


class RegistryFalso:
    """Lo que el ensamblador lee de un ``AgentRegistry``: regulares, subs y sus deltas."""

    def __init__(
        self,
        regulares: list[AgentConfig],
        subs: list[AgentConfig] | None = None,
        *,
        raw: dict[str, dict] | None = None,
    ) -> None:
        self._regulares = regulares
        self._subs = subs or []
        self._raw = raw if raw is not None else {s.id: _delta_minimo(s) for s in self._subs}

    def list_all(self) -> list[AgentConfig]:
        return [*self._regulares, *self._subs]

    def list_regular(self) -> list[AgentConfig]:
        return list(self._regulares)

    def list_sub_agents(self) -> list[AgentConfig]:
        return list(self._subs)

    def is_sub_agent(self, agent_id: str) -> bool:
        return any(s.id == agent_id for s in self._subs)

    def get_sub_agent_raw(self, agent_id: str) -> dict | None:
        return self._raw.get(agent_id)


def _delta_minimo(cfg: AgentConfig) -> dict:
    """Delta crudo de un sub-agente sin bloque ``llm``: el hijo efímero HEREDA el del caller."""
    return {
        "id": cfg.id,
        "name": cfg.name,
        "description": cfg.description,
        "system_prompt": cfg.system_prompt,
    }


@pytest.fixture
def home(tmp_path: Path) -> Iterator[Path]:
    """Home de instancia aislado: los ``RuntimePath`` del schema se anclan acá."""
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    set_inaki_home(tmp_path)
    try:
        yield tmp_path
    finally:
        set_inaki_home(None)


@pytest.fixture
def bordes_falsos(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """LLM y embedding falsos, parcheados donde se crean: el ``wiring.py`` de su módulo."""
    from inaki.embedding.wiring import EmbeddingProviderFactory
    from inaki.llm.wiring import LLMProviderFactory

    llm = AsyncMock()
    monkeypatch.setattr(LLMProviderFactory, "create", lambda *a, **k: llm)
    monkeypatch.setattr(EmbeddingProviderFactory, "create", lambda *a, **k: FakeEmbedder())
    return llm
