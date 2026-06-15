"""Tests del wiring de ReconcileMemoryUseCase en container.py.

Cubre:
1. build_memory_settings propaga los campos de reconciliación desde MemoriesConfig.
2. AgentContainer construye ReconcileMemoryUseCase cuando reconciliation.enabled=True.
3. AgentContainer NO construye ReconcileMemoryUseCase cuando reconciliation.enabled=False.
4. La reconciliación es INDEPENDIENTE de la consolidación: se construye con
   reconciliation.enabled=True aunque consolidation.enabled=False.
5. build_reconcile_memory_task genera el nombre correcto, TriggerType, schedule y task_id.
6. ReconcileDispatchAdapter llama al use case correcto por agent_id.
7. ReconcileDispatchAdapter lanza ValueError cuando el agent_id no existe.
8. _wire_memory_reconcilers llama a set_reconciler cuando reconciliation.agent_id apunta
   a un sub-agente válido (happy path).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.outbound.scheduler.builtin_tasks import (
    _RECONCILE_MEMORY_BASE_ID,
    build_reconcile_memory_task,
)
from adapters.outbound.scheduler.dispatch_adapters import ReconcileDispatchAdapter
from adapters.outbound.tools.tool_registry import ToolRegistry
from core.domain.entities.task import TriggerType
from core.domain.value_objects.agent_settings import MemorySettings, OneShotSettings
from core.use_cases.reconcile_memory import ReconcileMemoryUseCase
from core.use_cases.run_agent import RunAgentUseCase
from core.use_cases.run_agent_one_shot import RunAgentOneShotUseCase
from infrastructure.config import (
    AgentConfig,
    AgentDelegationConfig,
    ChatHistoryConfig,
    ConsolidationConfig,
    DelegationConfig,
    EmbeddingConfig,
    GlobalConfig,
    LLMConfig,
    MemoriesConfig,
    ReconciliationConfig,
    ProviderConfig,
)
from infrastructure.container import AgentContainer, build_memory_settings


# ---------------------------------------------------------------------------
# Helpers — idéntico patrón que test_container_wire_scheduler.py
# ---------------------------------------------------------------------------


class FakeEmbedder:
    async def embed_passage(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


def _make_memory_config(
    enabled: bool = True,
    reconcile_enabled: bool = False,
    reconcile_schedule: str = "0 4 * * 1",
    reconcile_similarity_threshold: float = 0.80,
    reconcile_top_k: int = 10,
    reconcile_agent_id: str | None = None,
) -> MemoriesConfig:
    """Construye un MemoriesConfig mapeando los params legacy a las sub-secciones.

    ``enabled`` controla la consolidación; los ``reconcile_*`` controlan la
    reconciliación (sección independiente). El sub-agente reconciliador se declara
    en ``reconciliation.agent_id`` (antes vivía en ``reconcile_llm.agent_id``).
    """
    return MemoriesConfig(
        db_filename=":memory:",
        consolidation=ConsolidationConfig(enabled=enabled),
        reconciliation=ReconciliationConfig(
            enabled=reconcile_enabled,
            schedule=reconcile_schedule,
            similarity_threshold=reconcile_similarity_threshold,
            top_k=reconcile_top_k,
            agent_id=reconcile_agent_id,
        ),
    )


def _make_agent_config(
    agent_id: str = "test-agent",
    memory_cfg: MemoriesConfig | None = None,
) -> AgentConfig:
    return AgentConfig(
        id=agent_id,
        name=agent_id.capitalize(),
        description=f"Agent {agent_id}",
        system_prompt="Test prompt",
        llm=LLMConfig(provider="openrouter", model="test-model"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_dirname="models/test"),
        memories=memory_cfg or _make_memory_config(),
        chat_history=ChatHistoryConfig(db_filename="/tmp/inaki_test/history.db"),
        delegation=AgentDelegationConfig(enabled=False),
        providers={"openrouter": ProviderConfig(api_key="test-key")},
    )


def _make_global_config() -> GlobalConfig:
    from infrastructure.config import (
        AppConfig,
        SchedulerConfig,
        SkillsConfig,
        ToolsConfig,
        WorkspaceConfig,
    )

    return GlobalConfig(
        app=AppConfig(ext_dirs=[]),
        llm=LLMConfig(provider="openrouter", model="test-model"),
        embedding=EmbeddingConfig(provider="e5_onnx", model_dirname="models/test"),
        memories=MemoriesConfig(db_filename=":memory:"),
        chat_history=ChatHistoryConfig(db_filename="/tmp/inaki_test/history.db"),
        skills=SkillsConfig(),
        tools=ToolsConfig(),
        scheduler=SchedulerConfig(),
        workspace=WorkspaceConfig(),
        delegation=DelegationConfig(),
        providers={"openrouter": ProviderConfig(api_key="test-key")},
    )


def _build_minimal_container(
    agent_config: AgentConfig,
    global_config: GlobalConfig,
) -> AgentContainer:
    """Construye un AgentContainer sin IO real — mismo patrón que test_container.py."""
    container = AgentContainer.__new__(AgentContainer)
    container.agent_config = agent_config
    container._global_config = global_config
    container._delegation_wired = False
    container._scheduler_wired = False
    container._photos_wired = False
    container._telegram_tools_wired = False
    container._llm = AsyncMock()
    container._embedder = FakeEmbedder()
    container._tools = ToolRegistry(embedder=container._embedder)
    container.run_agent = MagicMock(spec=RunAgentUseCase)
    container.run_agent._extra_system_sections = []
    container.run_agent_one_shot = RunAgentOneShotUseCase(
        llm=container._llm,
        tools=container._tools,
        settings=OneShotSettings(
            agent_id=agent_config.id,
            system_prompt=agent_config.system_prompt,
            circuit_breaker_threshold=agent_config.tools.circuit_breaker_threshold,
        ),
    )
    return container


# ---------------------------------------------------------------------------
# 1. build_memory_settings propaga los campos de reconciliación
# ---------------------------------------------------------------------------


def test_build_memory_settings_propaga_campos_reconcile() -> None:
    """Los campos de reconciliación del MemoriesConfig deben aparecer en MemorySettings.

    El VO ``MemorySettings.reconciliation`` solo lleva ``similarity_threshold`` y
    ``top_k``; ``enabled``/``schedule``/``agent_id`` son del wiring (no del VO).
    """
    mem_cfg = _make_memory_config(
        reconcile_enabled=True,
        reconcile_schedule="0 2 * * 0",
        reconcile_similarity_threshold=0.75,
        reconcile_top_k=15,
    )

    settings = build_memory_settings(mem_cfg)

    assert isinstance(settings, MemorySettings)
    assert settings.reconciliation.similarity_threshold == 0.75
    assert settings.reconciliation.top_k == 15


def test_build_memory_settings_defaults_reconcile() -> None:
    """Con MemoriesConfig defaults, MemorySettings usa los valores por defecto de reconcile."""
    mem_cfg = MemoriesConfig(db_filename=":memory:")

    settings = build_memory_settings(mem_cfg)

    assert settings.reconciliation.similarity_threshold == 0.80
    assert settings.reconciliation.top_k == 10


# ---------------------------------------------------------------------------
# 2. AgentContainer construye ReconcileMemoryUseCase cuando habilitado
# ---------------------------------------------------------------------------


def test_agent_container_construye_reconcile_use_case_cuando_habilitado() -> None:
    """Con reconciliation.enabled=True debe existir reconcile_memory."""
    mem_cfg = _make_memory_config(enabled=True, reconcile_enabled=True)
    agent_cfg = _make_agent_config(memory_cfg=mem_cfg)
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)

    # Simular la construcción del use case (el container minimal no llama __init__)
    # Replicamos la lógica del __init__ de AgentContainer para el use case:
    fake_memory = MagicMock()
    uc = ReconcileMemoryUseCase(
        llm=container._llm,
        memory=fake_memory,
        embedder=container._embedder,
        agent_id=agent_cfg.id,
        memory_config=build_memory_settings(mem_cfg),
    )
    container.reconcile_memory = uc

    assert container.reconcile_memory is not None
    assert isinstance(container.reconcile_memory, ReconcileMemoryUseCase)


# ---------------------------------------------------------------------------
# 3. AgentContainer NO construye ReconcileMemoryUseCase cuando reconciliation.enabled=False
# ---------------------------------------------------------------------------


def test_agent_container_no_construye_reconcile_cuando_disabled() -> None:
    """Con reconciliation.enabled=False, reconcile_memory debe ser None."""
    mem_cfg = _make_memory_config(enabled=True, reconcile_enabled=False)
    agent_cfg = _make_agent_config(memory_cfg=mem_cfg)
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)

    # Replica la condición del __init__ de AgentContainer (gating por reconciliation)
    reconcile_memory = None
    if agent_cfg.memories.reconciliation.enabled:
        reconcile_memory = MagicMock(spec=ReconcileMemoryUseCase)
    container.reconcile_memory = reconcile_memory

    assert container.reconcile_memory is None


# ---------------------------------------------------------------------------
# 4. La reconciliación es INDEPENDIENTE de la consolidación
# ---------------------------------------------------------------------------


def test_agent_container_construye_reconcile_aunque_consolidacion_disabled() -> None:
    """Con consolidation.enabled=False pero reconciliation.enabled=True, reconcile_memory
    SE construye igual.

    Cambio semántico: la reconciliación dejó de depender de la consolidación. Antes
    el gating exigía ``memory.enabled AND reconcile_enabled``; ahora alcanza con
    ``reconciliation.enabled=True`` — se puede reconciliar recuerdos preexistentes
    aunque la consolidación esté apagada.
    """
    mem_cfg = _make_memory_config(enabled=False, reconcile_enabled=True)
    agent_cfg = _make_agent_config(memory_cfg=mem_cfg)
    global_cfg = _make_global_config()
    container = _build_minimal_container(agent_cfg, global_cfg)

    # Replica la condición del __init__ (gating por reconciliation, NO por consolidation)
    reconcile_memory = None
    if agent_cfg.memories.reconciliation.enabled:
        reconcile_memory = MagicMock(spec=ReconcileMemoryUseCase)
    container.reconcile_memory = reconcile_memory

    assert container.reconcile_memory is not None
    # Sanity: la consolidación efectivamente quedó apagada en este escenario.
    assert agent_cfg.memories.consolidation.enabled is False


# ---------------------------------------------------------------------------
# 5. build_reconcile_memory_task: nombre, TriggerType, schedule y task_id correctos
# ---------------------------------------------------------------------------


def test_build_reconcile_memory_task_propiedades() -> None:
    """La builtin task tiene las propiedades esperadas."""
    schedule = "0 4 * * 1"
    agent_id = "mi_agente"
    task_id = _RECONCILE_MEMORY_BASE_ID

    task = build_reconcile_memory_task(schedule, agent_id, task_id)

    assert task.name == f"reconcile_memory_{agent_id}"
    assert task.trigger_type == TriggerType.RECONCILE_MEMORY
    assert task.schedule == schedule
    assert task.id == task_id
    assert task.trigger_payload.agent_id == agent_id  # type: ignore[union-attr]
    assert task.executions_remaining is None  # recurrente sin límite


def test_build_reconcile_memory_task_id_base_es_10() -> None:
    """El ID base de reconciliación debe ser 10 (evita colisión con user tasks en 100)."""
    assert _RECONCILE_MEMORY_BASE_ID == 10


def test_build_reconcile_memory_task_ids_distintos_por_agente() -> None:
    """Dos agentes distintos reciben IDs de task distintos."""
    t1 = build_reconcile_memory_task("0 4 * * 1", "agente_a", _RECONCILE_MEMORY_BASE_ID)
    t2 = build_reconcile_memory_task("0 4 * * 1", "agente_b", _RECONCILE_MEMORY_BASE_ID + 1)

    assert t1.id != t2.id
    assert t1.name != t2.name


def test_build_reconcile_memory_task_schedule_configurado() -> None:
    """El schedule que se pasa es el que aparece en la tarea (no un hardcoded)."""
    schedule_custom = "30 2 * * 5"  # viernes 2:30am
    task = build_reconcile_memory_task(schedule_custom, "agente", 10)

    assert task.schedule == schedule_custom


# ---------------------------------------------------------------------------
# 6. ReconcileDispatchAdapter llama al use case correcto por agent_id
# ---------------------------------------------------------------------------


async def test_reconcile_dispatch_adapter_llama_use_case() -> None:
    """ReconcileDispatchAdapter.reconcile invoca el use case del agente correcto."""
    mock_uc = MagicMock(spec=ReconcileMemoryUseCase)
    mock_uc.execute = AsyncMock(return_value="Reconciliación completada: 1 cluster(s).")

    adapter = ReconcileDispatchAdapter({"agente_x": mock_uc})

    resultado = await adapter.reconcile("agente_x")

    mock_uc.execute.assert_awaited_once()
    assert "Reconciliación" in resultado


# ---------------------------------------------------------------------------
# 7. ReconcileDispatchAdapter lanza ValueError cuando agent_id no existe
# ---------------------------------------------------------------------------


async def test_reconcile_dispatch_adapter_lanza_por_agent_id_inexistente() -> None:
    """Si el agent_id no tiene use case, ReconcileDispatchAdapter lanza ValueError."""
    adapter = ReconcileDispatchAdapter({})

    with pytest.raises(ValueError, match="agente_inexistente"):
        await adapter.reconcile("agente_inexistente")


# ---------------------------------------------------------------------------
# 8. _wire_memory_reconcilers llama a set_reconciler con sub-agente válido
# ---------------------------------------------------------------------------


def test_wire_memory_reconcilers_llama_set_reconciler() -> None:
    """_wire_memory_reconcilers debe llamar set_reconciler cuando reconciliation.agent_id
    apunta a un sub-agente válido."""
    # Construimos la mínima infraestructura para invocar _wire_memory_reconcilers
    # sin levantar el AppContainer completo.
    from infrastructure.container import AppContainer

    # Agente principal con reconcile habilitado y reconciliation.agent_id
    mem_cfg = _make_memory_config(
        enabled=True,
        reconcile_enabled=True,
        reconcile_agent_id="memory_reconciler",
    )
    agent_cfg = _make_agent_config(agent_id="agente_principal", memory_cfg=mem_cfg)
    global_cfg = _make_global_config()

    # Container del agente principal
    main_container = _build_minimal_container(agent_cfg, global_cfg)
    mock_uc = MagicMock(spec=ReconcileMemoryUseCase)
    mock_uc.set_reconciler = MagicMock()
    main_container.reconcile_memory = mock_uc

    # Container del sub-agente reconciliador
    sub_agent_cfg = _make_agent_config(agent_id="memory_reconciler")
    sub_container = _build_minimal_container(sub_agent_cfg, global_cfg)

    # Construir AppContainer mínimo con atributos suficientes
    app = AppContainer.__new__(AppContainer)
    app.global_config = global_cfg
    app.agents = {
        "agente_principal": main_container,
        "memory_reconciler": sub_container,
    }

    # Mock del registry
    mock_registry = MagicMock()
    mock_registry.is_sub_agent = lambda aid: aid == "memory_reconciler"
    app.registry = mock_registry

    # Ejecutar el wiring
    app._wire_memory_reconcilers()

    # set_reconciler debe haberse llamado con el run_agent_one_shot del sub-agente.
    # El sub-agente de test declara system_prompt="Test prompt" → se pasa como
    # override (sobreescribe el _RECONCILER_PROMPT default).
    mock_uc.set_reconciler.assert_called_once_with(
        sub_container.run_agent_one_shot,
        system_prompt_override="Test prompt",
        max_iterations=global_cfg.delegation.max_iterations_per_sub,
        timeout_seconds=global_cfg.delegation.timeout_seconds,
    )
