"""Unit tests para BackgroundTask y BackgroundTaskView (REQ-BGD-4).

BackgroundTask es la entidad in-memory que representa una delegación encolada en el
BackgroundDelegationQueueAdapter. BackgroundTaskView es el DTO read-only que
expone el adapter via snapshot_inflight, con un prompt_preview truncado a 80
chars (con elipsis Unicode "…") para inyectar en el system prompt del padre.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from core.domain.entities.background_task import BackgroundTask, BackgroundTaskView


def _make_task(
    *,
    id: str = "bg-1",
    prompt: str = "investigá el saldo de Galicia",
    started_at: datetime | None = None,
    status: str = "queued",
) -> BackgroundTask:
    return BackgroundTask(
        id=id,
        caller_agent_id="inaki",
        target_agent_id="researcher",
        prompt=prompt,
        system_prompt=None,
        channel="telegram",
        chat_id="42",
        started_at=started_at or datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc),
        status=status,  # type: ignore[arg-type]
    )


class TestBackgroundTask:
    def test_construye_con_todos_los_campos(self) -> None:
        task = _make_task()

        assert task.id == "bg-1"
        assert task.caller_agent_id == "inaki"
        assert task.target_agent_id == "researcher"
        assert task.prompt == "investigá el saldo de Galicia"
        assert task.system_prompt is None
        assert task.channel == "telegram"
        assert task.chat_id == "42"
        assert task.status == "queued"

    def test_status_solo_acepta_valores_literales(self) -> None:
        # status="completed" no está en el Literal — pydantic debe rechazarlo
        with pytest.raises(ValidationError):
            _make_task(status="completed")

    def test_es_mutable_para_transicion_de_estado(self) -> None:
        """El consumer del adapter muta status de 'queued' a 'running' en sitio."""
        task = _make_task(status="queued")

        task.status = "running"

        assert task.status == "running"


class TestBackgroundTaskViewFromTask:
    """Factory `from_task` construye un DTO truncando el prompt y calculando elapsed.

    Es la única forma documentada de obtener un View desde un Task — encapsula
    las reglas de truncado y elapsed.
    """

    def test_prompt_corto_se_mantiene_intacto(self) -> None:
        task = _make_task(prompt="dame el saldo")
        now = task.started_at + timedelta(seconds=5)

        view = BackgroundTaskView.from_task(task, now=now)

        assert view.prompt_preview == "dame el saldo"

    def test_prompt_de_exactamente_80_chars_se_mantiene_intacto(self) -> None:
        prompt_80 = "a" * 80
        task = _make_task(prompt=prompt_80)
        now = task.started_at + timedelta(seconds=1)

        view = BackgroundTaskView.from_task(task, now=now)

        assert view.prompt_preview == prompt_80
        assert len(view.prompt_preview) == 80

    def test_prompt_de_81_chars_se_trunca_con_elipsis(self) -> None:
        prompt_81 = "a" * 81
        task = _make_task(prompt=prompt_81)
        now = task.started_at + timedelta(seconds=1)

        view = BackgroundTaskView.from_task(task, now=now)

        # 79 chars + "…" = 80 caracteres totales
        assert view.prompt_preview == ("a" * 79) + "…"
        assert len(view.prompt_preview) == 80

    def test_prompt_muy_largo_se_trunca_a_80_con_elipsis(self) -> None:
        prompt_largo = "investigá esto en muchas fuentes y avisame con detalle " * 10
        task = _make_task(prompt=prompt_largo)
        now = task.started_at + timedelta(seconds=1)

        view = BackgroundTaskView.from_task(task, now=now)

        assert len(view.prompt_preview) == 80
        assert view.prompt_preview.endswith("…")
        assert view.prompt_preview[:-1] == prompt_largo[:79]

    def test_elapsed_seconds_truncado_a_int(self) -> None:
        task = _make_task()
        now = task.started_at + timedelta(seconds=42, milliseconds=750)

        view = BackgroundTaskView.from_task(task, now=now)

        assert view.elapsed_seconds == 42

    def test_elapsed_seconds_cero_cuando_now_igual_started_at(self) -> None:
        task = _make_task()
        now = task.started_at

        view = BackgroundTaskView.from_task(task, now=now)

        assert view.elapsed_seconds == 0

    def test_view_propaga_id_target_y_status(self) -> None:
        task = _make_task(id="bg-7", status="running")
        now = task.started_at + timedelta(seconds=10)

        view = BackgroundTaskView.from_task(task, now=now)

        assert view.id == "bg-7"
        assert view.target_agent_id == "researcher"
        assert view.status == "running"
