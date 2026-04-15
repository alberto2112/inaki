"""Tests para TaskLog — entidad de log de ejecución de tasks."""

from __future__ import annotations

from datetime import datetime, timezone

from core.domain.entities.task_log import TaskLog


def _fecha() -> datetime:
    return datetime(2026, 4, 15, 3, 0, 0, tzinfo=timezone.utc)


def test_metadata_default_es_none() -> None:
    log = TaskLog(task_id=1, started_at=_fecha(), status="success")
    assert log.metadata is None


def test_metadata_acepta_dict_arbitrario() -> None:
    metadata = {"original_target": "cli:local", "resolved_target": "file:///tmp/x.log"}
    log = TaskLog(task_id=1, started_at=_fecha(), status="success", metadata=metadata)
    assert log.metadata == metadata


def test_metadata_acepta_dict_vacio() -> None:
    log = TaskLog(task_id=1, started_at=_fecha(), status="success", metadata={})
    assert log.metadata == {}
