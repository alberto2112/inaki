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
8. _wire_memory_sub_agents llama a set_reconciler cuando reconciliation.agent_id apunta
   a un sub-agente válido (happy path).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from inaki.scheduler.adapters.builtin_tasks import (
    _RECONCILE_MEMORY_BASE_ID,
    build_reconcile_memory_task,
)
from inaki.scheduler.adapters.dispatch import ReconcileDispatchAdapter
from inaki.scheduler.domain.task import TriggerType
from inaki.kernel.domain.value_objects.agent_settings import MemorySettings
from inaki.config import (
    AgentConfig,
    AgentDelegationConfig,
    ChatHistoryConfig,
    ConsolidationConfig,
    DelegationConfig,
    EmbeddingConfig,
    GlobalConfig,
    LLMConfig,
    MemoriesConfig,
    ProviderConfig,
    ReconciliationConfig,
)
from inaki.memory.use_cases.reconcile_memory import ReconcileMemoryUseCase
from inaki.memory.wiring import (
    MemoryJobs,
    SubAgenteDeMemoria,
    build_memory_jobs,
    build_memory_settings,
    wire_sub_agentes_de_memoria,
)

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
    from inaki.config import (
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


# ---------------------------------------------------------------------------
# 3. AgentContainer NO construye ReconcileMemoryUseCase cuando reconciliation.enabled=False
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 4. La reconciliación es INDEPENDIENTE de la consolidación
# ---------------------------------------------------------------------------


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
# 8. _wire_memory_sub_agents llama a set_reconciler con sub-agente válido
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# build_memory_jobs — cada job se construye solo si SU flag está habilitado
# ---------------------------------------------------------------------------


def _jobs(mem_cfg: MemoriesConfig) -> MemoryJobs:
    return build_memory_jobs(
        _make_agent_config(memory_cfg=mem_cfg),
        base_llm=AsyncMock(),
        memory=MagicMock(),
        embedder=FakeEmbedder(),
        history=AsyncMock(),
    )


def test_reconcile_se_construye_cuando_esta_habilitado() -> None:
    jobs = _jobs(_make_memory_config(enabled=True, reconcile_enabled=True))
    assert isinstance(jobs.reconcile, ReconcileMemoryUseCase)
    assert jobs.consolidate is not None


def test_reconcile_no_se_construye_cuando_esta_deshabilitado() -> None:
    jobs = _jobs(_make_memory_config(enabled=True, reconcile_enabled=False))
    assert jobs.reconcile is None and jobs.consolidate is not None


def test_reconcile_se_construye_aunque_la_consolidacion_este_apagada() -> None:
    """La reconciliación dejó de depender de la consolidación: alcanza con
    ``reconciliation.enabled=True`` para reconciliar recuerdos preexistentes."""
    jobs = _jobs(_make_memory_config(enabled=False, reconcile_enabled=True))
    assert jobs.reconcile is not None and jobs.consolidate is None


# ---------------------------------------------------------------------------
# wire_sub_agentes_de_memoria — el reconciliador sub-agente llega por set_reconciler
# ---------------------------------------------------------------------------


def test_wire_sub_agentes_llama_set_reconciler_con_el_sub_agente_valido() -> None:
    mem_cfg = _make_memory_config(
        enabled=True, reconcile_enabled=True, reconcile_agent_id="memory_reconciler"
    )
    mock_uc = MagicMock(spec=ReconcileMemoryUseCase)
    mock_uc.set_reconciler = MagicMock()
    sub = SubAgenteDeMemoria(one_shot=MagicMock(), system_prompt="Test prompt")

    wire_sub_agentes_de_memoria(
        "agente_principal",
        MemoryJobs(consolidate=None, reconcile=mock_uc),
        mem_cfg,
        agentes={"memory_reconciler": sub},
        es_sub_agente=lambda aid: aid == "memory_reconciler",
        max_iterations=10,
        timeout_seconds=60,
    )

    # El sub declara system_prompt="Test prompt" → se pasa como override.
    mock_uc.set_reconciler.assert_called_once_with(
        sub.one_shot, system_prompt_override="Test prompt", max_iterations=10, timeout_seconds=60
    )


def test_wire_sub_agentes_loguea_error_si_el_id_no_es_un_sub_agente(caplog) -> None:
    mem_cfg = _make_memory_config(
        enabled=True, reconcile_enabled=True, reconcile_agent_id="regular"
    )
    mock_uc = MagicMock(spec=ReconcileMemoryUseCase)
    mock_uc.set_reconciler = MagicMock()

    with caplog.at_level("ERROR"):
        wire_sub_agentes_de_memoria(
            "agente_principal",
            MemoryJobs(consolidate=None, reconcile=mock_uc),
            mem_cfg,
            agentes={"regular": SubAgenteDeMemoria(one_shot=MagicMock(), system_prompt="")},
            es_sub_agente=lambda aid: False,
            max_iterations=10,
            timeout_seconds=60,
        )

    mock_uc.set_reconciler.assert_not_called()
    assert "debe apuntar a un sub-agente" in caplog.text
