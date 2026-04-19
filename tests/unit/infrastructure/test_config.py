"""Tests de resolución de paths de runtime y campos de MemoryConfig."""

from __future__ import annotations

from pathlib import Path


from infrastructure.config import (
    AppConfig,
    ChatHistoryConfig,
    EmbeddingConfig,
    MemoryConfig,
    SchedulerConfig,
)

HOME = str(Path.home())
INAKI_HOME = f"{HOME}/.inaki"


def test_default_digest_size() -> None:
    cfg = MemoryConfig()
    assert cfg.digest_size == 14


def test_explicit_digest_size() -> None:
    cfg = MemoryConfig(digest_size=20)
    assert cfg.digest_size == 20


# ---------------------------------------------------------------------------
# Defaults — anclados bajo ~/.inaki/
# ---------------------------------------------------------------------------


def test_default_memory_db_filename_resolves_to_inaki_home() -> None:
    cfg = MemoryConfig()
    assert cfg.db_filename == f"{INAKI_HOME}/data/inaki.db"


def test_default_digest_filename_resolves_to_inaki_home() -> None:
    cfg = MemoryConfig()
    assert cfg.digest_filename == f"{INAKI_HOME}/mem/last_memories.md"


def test_default_chat_history_db_filename_resolves_to_inaki_home() -> None:
    cfg = ChatHistoryConfig()
    assert cfg.db_filename == f"{INAKI_HOME}/data/history.db"


def test_default_scheduler_db_filename_resolves_to_inaki_home() -> None:
    cfg = SchedulerConfig()
    assert cfg.db_filename == f"{INAKI_HOME}/data/scheduler.db"


def test_default_embedding_model_dirname_resolves_to_inaki_home() -> None:
    cfg = EmbeddingConfig()
    assert cfg.model_dirname == f"{INAKI_HOME}/models/e5-small"


def test_default_embedding_cache_filename_resolves_to_inaki_home() -> None:
    cfg = EmbeddingConfig()
    assert cfg.cache_filename == f"{INAKI_HOME}/data/embedding_cache.db"


# ---------------------------------------------------------------------------
# RuntimePath — paths relativos se anclan a ~/.inaki/
# ---------------------------------------------------------------------------


def test_relative_path_anchored_under_inaki_home() -> None:
    cfg = MemoryConfig(db_filename="custom/inaki.db")
    assert cfg.db_filename == f"{INAKI_HOME}/custom/inaki.db"


def test_relative_bare_filename_anchored_under_inaki_home() -> None:
    cfg = MemoryConfig(db_filename="inaki.db")
    assert cfg.db_filename == f"{INAKI_HOME}/inaki.db"


def test_relative_digest_filename_anchored_under_inaki_home() -> None:
    cfg = MemoryConfig(digest_filename="digest.md")
    assert cfg.digest_filename == f"{INAKI_HOME}/digest.md"


# ---------------------------------------------------------------------------
# RuntimePath — paths absolutos se usan tal cual (escape hatch)
# ---------------------------------------------------------------------------


def test_absolute_path_used_as_is() -> None:
    cfg = MemoryConfig(db_filename="/srv/inaki/foo.db")
    assert cfg.db_filename == "/srv/inaki/foo.db"


def test_tilde_path_expands_to_absolute_and_not_anchored() -> None:
    """Tras expansión, el path ya es absoluto → no se prepende ~/.inaki/."""
    cfg = ChatHistoryConfig(db_filename="~/custom/history.db")
    assert cfg.db_filename == f"{HOME}/custom/history.db"


def test_absolute_scheduler_path_used_as_is() -> None:
    cfg = SchedulerConfig(db_filename="/var/lib/inaki/sched.db")
    assert cfg.db_filename == "/var/lib/inaki/sched.db"


def test_absolute_model_dirname_used_as_is() -> None:
    cfg = EmbeddingConfig(model_dirname="/opt/models/e5")
    assert cfg.model_dirname == "/opt/models/e5"


# ---------------------------------------------------------------------------
# RuntimePath — valor especial de SQLite :memory: no se interpreta como path
# ---------------------------------------------------------------------------


def test_sqlite_memory_special_passes_through() -> None:
    cfg = MemoryConfig(db_filename=":memory:")
    assert cfg.db_filename == ":memory:"


def test_sqlite_memory_special_in_history() -> None:
    cfg = ChatHistoryConfig(db_filename=":memory:")
    assert cfg.db_filename == ":memory:"


def test_sqlite_memory_special_in_scheduler() -> None:
    cfg = SchedulerConfig(db_filename=":memory:")
    assert cfg.db_filename == ":memory:"


# ---------------------------------------------------------------------------
# AppConfig.ext_dirs — expansión por elemento (sin anchoring runtime)
# ---------------------------------------------------------------------------


def test_app_ext_dirs_expand_tilde_per_element() -> None:
    cfg = AppConfig(ext_dirs=["ext", "~/.inaki/ext", "/abs/path"])
    assert cfg.ext_dirs[0] == "ext"
    assert cfg.ext_dirs[1] == f"{HOME}/.inaki/ext"
    assert cfg.ext_dirs[2] == "/abs/path"
    assert all("~" not in p for p in cfg.ext_dirs)


# ---------------------------------------------------------------------------
# keep_last_messages — sentinel 0 → fallback 84
# ---------------------------------------------------------------------------

def test_keep_last_messages_default_is_zero_sentinel() -> None:
    cfg = MemoryConfig()
    assert cfg.keep_last_messages == 0
    assert cfg.resolved_keep_last_messages() == 84


def test_keep_last_messages_explicit_value_respected() -> None:
    cfg = MemoryConfig(keep_last_messages=50)
    assert cfg.resolved_keep_last_messages() == 50


def test_keep_last_messages_negative_treated_as_sentinel() -> None:
    cfg = MemoryConfig(keep_last_messages=-1)
    assert cfg.resolved_keep_last_messages() == 84


# ---------------------------------------------------------------------------
# DelegationConfig — global defaults
# ---------------------------------------------------------------------------

from infrastructure.config import (  # noqa: E402
    AgentConfig,
    AgentDelegationConfig,
    DelegationConfig,
    GlobalConfig,
    LLMConfig,
    _render_default_global_yaml,
)


def test_delegation_config_default_max_iterations() -> None:
    cfg = DelegationConfig()
    assert cfg.max_iterations_per_sub == 10


def test_delegation_config_default_timeout_seconds() -> None:
    cfg = DelegationConfig()
    assert cfg.timeout_seconds == 60


def test_delegation_config_yaml_override_max_iterations() -> None:
    cfg = DelegationConfig(max_iterations_per_sub=5)
    assert cfg.max_iterations_per_sub == 5


def test_delegation_config_yaml_override_timeout_seconds() -> None:
    cfg = DelegationConfig(timeout_seconds=120)
    assert cfg.timeout_seconds == 120


# ---------------------------------------------------------------------------
# AgentDelegationConfig — per-agent defaults
# ---------------------------------------------------------------------------

def test_agent_delegation_config_default_enabled() -> None:
    cfg = AgentDelegationConfig()
    assert cfg.enabled is False


def test_agent_delegation_config_default_allowed_targets() -> None:
    cfg = AgentDelegationConfig()
    assert cfg.allowed_targets == []


def test_agent_delegation_config_override_enabled() -> None:
    cfg = AgentDelegationConfig(enabled=True)
    assert cfg.enabled is True


def test_agent_delegation_config_override_allowed_targets() -> None:
    cfg = AgentDelegationConfig(allowed_targets=["specialist", "researcher"])
    assert cfg.allowed_targets == ["specialist", "researcher"]


# ---------------------------------------------------------------------------
# GlobalConfig — delegation section wired
# ---------------------------------------------------------------------------

def _make_global_config(**delegation_kwargs) -> GlobalConfig:
    """Construye un GlobalConfig mínimo para tests, con delegation override opcional."""
    return GlobalConfig(
        app=AppConfig(),
        llm=LLMConfig(),
        embedding=EmbeddingConfig(),
        memory=MemoryConfig(),
        chat_history=ChatHistoryConfig(),
        delegation=DelegationConfig(**delegation_kwargs) if delegation_kwargs else DelegationConfig(),
    )


def test_global_config_delegation_default_when_absent() -> None:
    cfg = _make_global_config()
    assert cfg.delegation.max_iterations_per_sub == 10
    assert cfg.delegation.timeout_seconds == 60


def test_global_config_delegation_override_max_iterations() -> None:
    cfg = _make_global_config(max_iterations_per_sub=3)
    assert cfg.delegation.max_iterations_per_sub == 3


def test_global_config_delegation_override_timeout_seconds() -> None:
    cfg = _make_global_config(timeout_seconds=30)
    assert cfg.delegation.timeout_seconds == 30


def test_global_config_existing_fields_unaffected_by_delegation() -> None:
    cfg = _make_global_config()
    assert cfg.app.name == "Iñaki"
    assert cfg.llm.provider == "openrouter"
    assert cfg.scheduler.enabled is True


# ---------------------------------------------------------------------------
# AgentConfig — delegation section wired
# ---------------------------------------------------------------------------

def _make_agent_config(**delegation_kwargs) -> AgentConfig:
    """Construye un AgentConfig mínimo para tests."""
    return AgentConfig(
        id="test-agent",
        name="Test",
        description="testing",
        system_prompt="you are a test agent",
        llm=LLMConfig(),
        embedding=EmbeddingConfig(),
        memory=MemoryConfig(),
        chat_history=ChatHistoryConfig(),
        delegation=AgentDelegationConfig(**delegation_kwargs) if delegation_kwargs else AgentDelegationConfig(),
    )


def test_agent_config_delegation_default_disabled() -> None:
    cfg = _make_agent_config()
    assert cfg.delegation.enabled is False


def test_agent_config_delegation_default_allowed_targets_empty() -> None:
    cfg = _make_agent_config()
    assert cfg.delegation.allowed_targets == []


def test_agent_config_delegation_override_enabled_true() -> None:
    cfg = _make_agent_config(enabled=True)
    assert cfg.delegation.enabled is True


def test_agent_config_delegation_override_allowed_targets() -> None:
    cfg = _make_agent_config(enabled=True, allowed_targets=["agent-a", "agent-b"])
    assert cfg.delegation.allowed_targets == ["agent-a", "agent-b"]


def test_agent_config_existing_fields_unaffected_by_delegation() -> None:
    cfg = _make_agent_config()
    assert cfg.tools.tool_call_max_iterations == 5
    assert cfg.skills.rag_top_k == 3


# ---------------------------------------------------------------------------
# _render_default_global_yaml — delegation section present
# ---------------------------------------------------------------------------

def test_render_default_global_yaml_contains_delegation_section() -> None:
    """La sección comentada de delegation debe estar en el YAML generado."""
    rendered = _render_default_global_yaml()
    assert "delegation" in rendered
    assert "max_iterations_per_sub" in rendered
    assert "timeout_seconds" in rendered


def test_render_default_global_yaml_delegation_values_match_defaults() -> None:
    """Los valores comentados deben coincidir con los defaults de DelegationConfig."""
    rendered = _render_default_global_yaml()
    cfg = DelegationConfig()
    assert str(cfg.max_iterations_per_sub) in rendered  # "10"
    assert str(cfg.timeout_seconds) in rendered          # "60"


def test_render_default_global_yaml_delegation_is_commented_out() -> None:
    """La sección delegation en el render NO debe ser YAML activo — va comentada."""
    import yaml as _yaml
    rendered = _render_default_global_yaml()
    parsed = _yaml.safe_load(rendered)
    # El parser YAML no debe ver la clave "delegation" — está comentada
    assert "delegation" not in parsed
