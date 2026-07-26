"""Invariante de ScheduledTask: trigger_type ↔ trigger_payload.type.

La unión de payloads es discriminada por ``payload.type``, pero el dispatcher
rutea por la columna ``trigger_type``. Si divergen, la task se persiste
"válida" y ejecuta un trigger distinto del que declara — por eso el estado
incoherente no debe ser representable.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.domain.entities.task import (
    AgentSendPayload,
    ChannelSendPayload,
    ScheduledTask,
    ShellExecPayload,
    TaskKind,
    TriggerType,
)


def _task(trigger_type: TriggerType, payload: object) -> ScheduledTask:
    return ScheduledTask(
        name="t",
        task_kind=TaskKind.ONESHOT,
        trigger_type=trigger_type,
        trigger_payload=payload,  # type: ignore[arg-type]
        schedule="2026-04-12T14:00:00Z",
    )


@pytest.mark.parametrize(
    ("trigger_type", "payload"),
    [
        (TriggerType.CHANNEL_SEND, ChannelSendPayload(target="telegram:1", text="hi")),
        (TriggerType.AGENT_SEND, AgentSendPayload(agent_id="a", task="do it")),
        (TriggerType.SHELL_EXEC, ShellExecPayload(command="echo hi")),
    ],
)
def test_trigger_type_coherente_con_payload_se_acepta(
    trigger_type: TriggerType, payload: object
) -> None:
    task = _task(trigger_type, payload)
    assert task.trigger_payload.type == task.trigger_type.value


@pytest.mark.parametrize(
    ("trigger_type", "payload"),
    [
        (TriggerType.AGENT_SEND, ChannelSendPayload(target="telegram:1", text="hi")),
        (TriggerType.CHANNEL_SEND, ShellExecPayload(command="echo hi")),
        (TriggerType.SHELL_EXEC, AgentSendPayload(agent_id="a", task="do it")),
    ],
)
def test_trigger_type_incoherente_con_payload_es_rechazado(
    trigger_type: TriggerType, payload: object
) -> None:
    with pytest.raises(ValidationError, match="no coincide con trigger_type"):
        _task(trigger_type, payload)


def test_mensaje_de_error_nombra_ambos_valores() -> None:
    """El error debe ser accionable: qué tipo dice el payload y qué dice la task."""
    with pytest.raises(ValidationError) as exc:
        _task(TriggerType.AGENT_SEND, ChannelSendPayload(target="telegram:1", text="hi"))

    message = str(exc.value)
    assert "channel_send" in message
    assert "agent_send" in message
