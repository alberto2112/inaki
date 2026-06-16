"""Test end-to-end del re-anclaje de instancia (``--home`` / ``INAKI_HOME``).

Con un home custom, TODOS los paths ``RuntimePath`` de las configs + los derivados del
home (users, tool_config, secret.key) caen bajo ese home. Sin override, bajo ``~/.inaki``.

Es la red de seguridad de B: si alguien rompe el re-anclaje de un recurso (vuelve a
hardcodear ``~/.inaki`` o un default de clase eager), este test lo caza.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure import config_schema as cs
from infrastructure.home import get_inaki_home, set_inaki_home


@pytest.fixture(autouse=True)
def _reset_home_override():
    set_inaki_home(None)
    yield
    set_inaki_home(None)


def test_todos_los_paths_reanclan_con_home(tmp_path, monkeypatch):
    """Con ``set_inaki_home(tmp)``, cada RuntimePath + derivado cae bajo ``tmp``."""
    monkeypatch.delenv("INAKI_HOME", raising=False)
    set_inaki_home(tmp_path)

    # Campos RuntimePath de cada config (se resuelven frescos contra el home)
    assert cs.MemoriesConfig().db_filename == str(tmp_path / "data" / "inaki.db")
    assert cs.ChatHistoryConfig().db_filename == str(tmp_path / "data" / "history.db")
    assert cs.SchedulerConfig().db_filename == str(tmp_path / "data" / "scheduler.db")
    assert cs.SchedulerConfig().fallback_log_filename == str(
        tmp_path / "data" / "scheduler-fallback.log"
    )
    assert cs.EmbeddingConfig().cache_filename == str(tmp_path / "data" / "embedding_cache.db")
    assert cs.KnowledgeConfig().db_dirname == str(tmp_path / "knowledge")

    # Derivados del home que arma el composition root (container / builders)
    assert get_inaki_home() / "users" == tmp_path / "users"
    assert get_inaki_home() / "config" / "tool_config.yaml" == tmp_path / "config" / "tool_config.yaml"
    assert get_inaki_home() / "secret.key" == tmp_path / "secret.key"


def test_eager_defaults_de_global_reanclan(tmp_path, monkeypatch):
    """Los configs con RuntimePath usados como default de ``GlobalConfig`` (scheduler,
    knowledge) reanclan via ``default_factory`` aunque NO se pasen al construir."""
    monkeypatch.delenv("INAKI_HOME", raising=False)
    set_inaki_home(tmp_path)

    sched_factory = cs.GlobalConfig.model_fields["scheduler"].default_factory
    know_factory = cs.GlobalConfig.model_fields["knowledge"].default_factory
    assert sched_factory is not None and know_factory is not None
    assert sched_factory().db_filename == str(tmp_path / "data" / "scheduler.db")
    assert know_factory().db_dirname == str(tmp_path / "knowledge")


def test_default_sin_override_queda_en_inaki(monkeypatch):
    """Sin override ni env, todo aterriza bajo ``~/.inaki`` (backward-compat sagrado)."""
    monkeypatch.delenv("INAKI_HOME", raising=False)
    default = Path.home() / ".inaki"

    assert cs.MemoriesConfig().db_filename == str(default / "data" / "inaki.db")
    assert cs.KnowledgeConfig().db_dirname == str(default / "knowledge")
    assert get_inaki_home() == default


def test_env_inaki_home_reancla(tmp_path, monkeypatch):
    """``INAKI_HOME`` env (sin flag) también reancla — el path del daemon systemd."""
    monkeypatch.setenv("INAKI_HOME", str(tmp_path))
    assert cs.MemoriesConfig().db_filename == str(tmp_path / "data" / "inaki.db")
    assert get_inaki_home() == tmp_path
