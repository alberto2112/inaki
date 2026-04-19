"""
Unit tests for adapters/outbound/tools/scheduler_tool.py — SchedulerTool.

Coverage:
- create: happy path (one_shot relative, one_shot ISO, recurring cron), all trigger types
- list: response shape {"tasks": [...], "total": N}
- get: happy path + TaskNotFoundError
- update: happy path + BuiltinTaskProtectedError
- delete: happy path + BuiltinTaskProtectedError
- Validation rules:
  - recurring + relative format → error
  - zero-duration schedule → error (parse_schedule ValueError)
  - unknown operation → error
  - invalid trigger_payload → error
- Error handling:
  - TooManyActiveTasksError → ToolResult(success=False)
  - TaskNotFoundError → ToolResult(success=False)
  - BuiltinTaskProtectedError → ToolResult(success=False)
  - Unexpected exception → ToolResult(success=False) with generic message
- created_by is ALWAYS agent_id from constructor, never from kwargs
- T4: channel_send → target auto-inyectado desde ChannelContext.routing_key
- T4: channel_send + user_id override → target reconstruido con channel_type del contexto
- T4: channel_send sin contexto → error descriptivo
- T4: trigger no channel_send → sin inyección (comportamiento existente)
- T4: LLM envía 'target' en payload → silenciosamente descartado
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.outbound.tools.scheduler_tool import SchedulerTool
from core.domain.entities.task import (
    AgentSendPayload,
    ChannelSendPayload,
    ScheduledTask,
    ShellExecPayload,
    TaskKind,
    TaskStatus,
    TriggerType,
)
from core.domain.errors import (
    BuiltinTaskProtectedError,
    SchedulerError,
    TaskNotFoundError,
    TooManyActiveTasksError,
)
from core.domain.value_objects.channel_context import ChannelContext
from core.ports.outbound.tool_port import ToolResult


# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------

_AGENT_ID = "test-agent"
_USER_TZ = "UTC"
_DEFAULT_CHANNEL_CTX = ChannelContext(channel_type="telegram", user_id="123456")


def _make_tool(
    agent_id: str = _AGENT_ID,
    user_timezone: str = _USER_TZ,
    uc: MagicMock | None = None,
    get_channel_context=None,
) -> tuple[SchedulerTool, MagicMock]:
    """Returns (tool, mock_uc). mock_uc has all methods as AsyncMock by default."""
    if uc is None:
        uc = MagicMock()
        uc.create_task = AsyncMock()
        uc.list_tasks = AsyncMock()
        uc.get_task = AsyncMock()
        uc.update_task = AsyncMock()
        uc.delete_task = AsyncMock()
    # Por defecto usa el contexto de canal estándar de prueba
    if get_channel_context is None:
        def get_channel_context() -> ChannelContext:
            return _DEFAULT_CHANNEL_CTX
    tool = SchedulerTool(
        schedule_task_uc=uc,
        agent_id=agent_id,
        user_timezone=user_timezone,
        get_channel_context=get_channel_context,
    )
    return tool, uc


def _make_task(
    task_id: int = 42,
    name: str = "Test task",
    task_kind: TaskKind = TaskKind.ONESHOT,
    trigger_type: TriggerType = TriggerType.CHANNEL_SEND,
    created_by: str = _AGENT_ID,
) -> ScheduledTask:
    return ScheduledTask(
        id=task_id,
        name=name,
        task_kind=task_kind,
        trigger_type=trigger_type,
        trigger_payload=ChannelSendPayload(target="telegram:ch1", text="hello"),
        schedule="2026-04-12T14:00:00Z",
        created_by=created_by,
        created_at=datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# create — happy paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_one_shot_relative_schedule() -> None:
    """create with '+2h' relative schedule → parses to ISO and calls use case."""
    tool, uc = _make_tool()
    created = _make_task(task_id=1, name="My Task")
    uc.create_task.return_value = created

    result = await tool.execute(
        operation="create",
        name="My Task",
        task_kind="one_shot",
        trigger_type="channel_send",
        trigger_payload={"text": "hello"},
        schedule="+2h",
    )

    assert result.success is True
    data = json.loads(result.output)
    assert data["id"] == 1
    assert data["name"] == "My Task"
    # Verify use case was called once
    uc.create_task.assert_awaited_once()
    # Verify created_by was injected from agent_id, NOT from kwargs
    call_arg: ScheduledTask = uc.create_task.call_args[0][0]
    assert call_arg.created_by == _AGENT_ID


@pytest.mark.asyncio
async def test_create_one_shot_iso_schedule() -> None:
    """create with ISO 8601 schedule → passes through to use case."""
    tool, uc = _make_tool()
    created = _make_task(task_id=2, name="ISO Task")
    uc.create_task.return_value = created

    result = await tool.execute(
        operation="create",
        name="ISO Task",
        task_kind="one_shot",
        trigger_type="channel_send",
        trigger_payload={"text": "ping"},
        schedule="2026-06-01T10:00:00Z",
    )

    assert result.success is True
    uc.create_task.assert_awaited_once()
    call_arg: ScheduledTask = uc.create_task.call_args[0][0]
    # Schedule passed through as-is (ISO raw string for one_shot)
    assert call_arg.schedule == "2026-06-01T10:00:00Z"
    assert call_arg.created_by == _AGENT_ID


@pytest.mark.asyncio
async def test_create_recurring_cron_schedule() -> None:
    """create recurring with cron expression → task_kind maps to 'recurrent'."""
    tool, uc = _make_tool()
    created = _make_task(task_id=3, name="Cron Task", task_kind=TaskKind.RECURRENT)
    uc.create_task.return_value = created

    result = await tool.execute(
        operation="create",
        name="Cron Task",
        task_kind="recurring",
        trigger_type="channel_send",
        trigger_payload={"text": "daily"},
        schedule="0 8 * * *",
    )

    assert result.success is True
    call_arg: ScheduledTask = uc.create_task.call_args[0][0]
    assert call_arg.task_kind == TaskKind.RECURRENT
    assert call_arg.schedule == "0 8 * * *"


@pytest.mark.asyncio
async def test_create_trigger_type_agent_send() -> None:
    """create with agent_send trigger_type → AgentSendPayload validated correctly."""
    tool, uc = _make_tool()
    task = _make_task(task_id=4, trigger_type=TriggerType.AGENT_SEND)
    # Override payload with agent_send
    task = task.model_copy(update={
        "trigger_payload": AgentSendPayload(agent_id="other-agent", task="do something")
    })
    uc.create_task.return_value = task

    result = await tool.execute(
        operation="create",
        name="Agent Task",
        task_kind="one_shot",
        trigger_type="agent_send",
        trigger_payload={"agent_id": "other-agent", "task": "do something"},
        schedule="2026-06-01T10:00:00Z",
    )

    assert result.success is True
    call_arg: ScheduledTask = uc.create_task.call_args[0][0]
    assert isinstance(call_arg.trigger_payload, AgentSendPayload)
    assert call_arg.trigger_payload.agent_id == "other-agent"


@pytest.mark.asyncio
async def test_create_trigger_type_shell_exec() -> None:
    """create with shell_exec trigger_type → ShellExecPayload validated correctly."""
    tool, uc = _make_tool()
    task = _make_task(task_id=5, trigger_type=TriggerType.SHELL_EXEC)
    task = task.model_copy(update={
        "trigger_payload": ShellExecPayload(command="echo hello")
    })
    uc.create_task.return_value = task

    result = await tool.execute(
        operation="create",
        name="Shell Task",
        task_kind="one_shot",
        trigger_type="shell_exec",
        trigger_payload={"command": "echo hello"},
        schedule="2026-06-01T10:00:00Z",
    )

    assert result.success is True
    call_arg: ScheduledTask = uc.create_task.call_args[0][0]
    assert isinstance(call_arg.trigger_payload, ShellExecPayload)
    assert call_arg.trigger_payload.command == "echo hello"


@pytest.mark.asyncio
async def test_create_created_by_always_from_agent_id_not_kwargs() -> None:
    """created_by must be injected from constructor agent_id, not from LLM kwargs."""
    tool, uc = _make_tool(agent_id="injected-agent")
    created = _make_task(task_id=6, created_by="injected-agent")
    uc.create_task.return_value = created

    # Pass a created_by in kwargs — must be ignored
    result = await tool.execute(
        operation="create",
        name="Task",
        task_kind="one_shot",
        trigger_type="channel_send",
        trigger_payload={"text": "hi"},
        schedule="2026-06-01T10:00:00Z",
        created_by="malicious-agent",  # must be ignored
    )

    assert result.success is True
    call_arg: ScheduledTask = uc.create_task.call_args[0][0]
    assert call_arg.created_by == "injected-agent"
    assert call_arg.created_by != "malicious-agent"


# ---------------------------------------------------------------------------
# list — response shape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_returns_correct_shape() -> None:
    """list → {"tasks": [...], "total": N} with correct field mapping."""
    tool, uc = _make_tool()
    tasks = [
        _make_task(task_id=1, name="Task 1"),
        _make_task(task_id=2, name="Task 2"),
        _make_task(task_id=3, name="Task 3", task_kind=TaskKind.RECURRENT),
    ]
    uc.list_tasks.return_value = tasks

    result = await tool.execute(operation="list")

    assert result.success is True
    data = json.loads(result.output)
    assert "tasks" in data
    assert "total" in data
    assert data["total"] == 3
    assert len(data["tasks"]) == 3

    # Verify one_shot kind mapping
    first = data["tasks"][0]
    assert first["id"] == 1
    assert first["name"] == "Task 1"
    assert first["task_kind"] == "one_shot"

    # Verify recurring kind mapping
    third = data["tasks"][2]
    assert third["task_kind"] == "recurring"


@pytest.mark.asyncio
async def test_list_empty_returns_zero_total() -> None:
    """list with no tasks → {"tasks": [], "total": 0}."""
    tool, uc = _make_tool()
    uc.list_tasks.return_value = []

    result = await tool.execute(operation="list")

    assert result.success is True
    data = json.loads(result.output)
    assert data["tasks"] == []
    assert data["total"] == 0


# ---------------------------------------------------------------------------
# get — happy path + TaskNotFoundError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_happy_path() -> None:
    """get by id → full detail including trigger_payload, schedule, created_by."""
    tool, uc = _make_tool()
    task = _make_task(task_id=10, name="Detail Task")
    uc.get_task.return_value = task

    result = await tool.execute(operation="get", task_id=10)

    assert result.success is True
    data = json.loads(result.output)
    assert data["id"] == 10
    assert data["name"] == "Detail Task"
    assert "trigger_payload" in data
    assert "schedule" in data
    assert "created_by" in data
    assert "created_at" in data
    uc.get_task.assert_awaited_once_with(10)


@pytest.mark.asyncio
async def test_get_task_not_found() -> None:
    """get with unknown task_id → ToolResult(success=False)."""
    tool, uc = _make_tool()
    uc.get_task.side_effect = TaskNotFoundError("Task 99 not found")

    result = await tool.execute(operation="get", task_id=99)

    assert result.success is False
    assert "99" in result.output or "not found" in result.output.lower()


@pytest.mark.asyncio
async def test_get_missing_task_id() -> None:
    """get without task_id → validation error."""
    tool, uc = _make_tool()

    result = await tool.execute(operation="get")

    assert result.success is False
    assert "task_id" in result.output


# ---------------------------------------------------------------------------
# update — happy path + BuiltinTaskProtectedError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_happy_path() -> None:
    """update name → ToolResult(success=True) with id and name."""
    tool, uc = _make_tool()
    updated = _make_task(task_id=20, name="Updated Name")
    uc.update_task.return_value = updated

    result = await tool.execute(operation="update", task_id=20, name="Updated Name")

    assert result.success is True
    data = json.loads(result.output)
    assert data["id"] == 20
    assert data["name"] == "Updated Name"
    uc.update_task.assert_awaited_once_with(20, name="Updated Name")


@pytest.mark.asyncio
async def test_update_builtin_task_protected() -> None:
    """update builtin task (id < 100) → BuiltinTaskProtectedError → ToolResult(success=False)."""
    tool, uc = _make_tool()
    uc.update_task.side_effect = BuiltinTaskProtectedError("Task 1 is builtin")

    result = await tool.execute(operation="update", task_id=1, name="new name")

    assert result.success is False
    assert "builtin" in result.output.lower() or "protected" in result.output.lower() or "1" in result.output


@pytest.mark.asyncio
async def test_update_no_mutable_fields() -> None:
    """update with no recognized mutable fields → validation error."""
    tool, uc = _make_tool()

    result = await tool.execute(operation="update", task_id=5)

    assert result.success is False
    assert "mutable" in result.output.lower() or "field" in result.output.lower()


@pytest.mark.asyncio
async def test_update_relative_schedule_parsed() -> None:
    """update with '+1h' schedule → schedule resolved to ISO string."""
    tool, uc = _make_tool()
    updated = _make_task(task_id=30, name="Task")
    uc.update_task.return_value = updated

    result = await tool.execute(operation="update", task_id=30, schedule="+1h")

    assert result.success is True
    # The schedule argument passed to update_task must be an ISO string (parsed)
    kwargs = uc.update_task.call_args[1]
    assert "schedule" in kwargs
    sched = kwargs["schedule"]
    # Must be a valid ISO datetime string, not the raw "+1h"
    assert sched != "+1h"
    assert "T" in sched  # ISO 8601 has a T separator


# ---------------------------------------------------------------------------
# delete — happy path + BuiltinTaskProtectedError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_happy_path() -> None:
    """delete existing task → ToolResult(success=True) with deleted=True."""
    tool, uc = _make_tool()
    uc.delete_task.return_value = None

    result = await tool.execute(operation="delete", task_id=50)

    assert result.success is True
    data = json.loads(result.output)
    assert data["deleted"] is True
    assert data["task_id"] == 50
    uc.delete_task.assert_awaited_once_with(50)


@pytest.mark.asyncio
async def test_delete_builtin_task_protected() -> None:
    """delete builtin task → BuiltinTaskProtectedError → ToolResult(success=False)."""
    tool, uc = _make_tool()
    uc.delete_task.side_effect = BuiltinTaskProtectedError("Task 1 is protected")

    result = await tool.execute(operation="delete", task_id=1)

    assert result.success is False


@pytest.mark.asyncio
async def test_delete_task_not_found() -> None:
    """delete unknown task → TaskNotFoundError → ToolResult(success=False)."""
    tool, uc = _make_tool()
    uc.delete_task.side_effect = TaskNotFoundError("Task not found")

    result = await tool.execute(operation="delete", task_id=999)

    assert result.success is False


@pytest.mark.asyncio
async def test_delete_missing_task_id() -> None:
    """delete without task_id → validation error."""
    tool, uc = _make_tool()

    result = await tool.execute(operation="delete")

    assert result.success is False
    assert "task_id" in result.output


# ---------------------------------------------------------------------------
# Validation rules
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_recurring_with_relative_schedule_is_error() -> None:
    """recurring + '+5h' relative schedule → error (cron required)."""
    tool, uc = _make_tool()

    result = await tool.execute(
        operation="create",
        name="Bad Recurring",
        task_kind="recurring",
        trigger_type="channel_send",
        trigger_payload={"text": "hello"},
        schedule="+5h",
    )

    assert result.success is False
    assert "cron" in result.output.lower() or "recurring" in result.output.lower()
    uc.create_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_zero_duration_schedule_is_error() -> None:
    """'+0m' zero-duration schedule → ValueError from parse_schedule → error."""
    tool, uc = _make_tool()

    result = await tool.execute(
        operation="create",
        name="Zero Task",
        task_kind="one_shot",
        trigger_type="channel_send",
        trigger_payload={"text": "hi"},
        schedule="+0m",
    )

    assert result.success is False
    uc.create_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_unknown_operation_is_error() -> None:
    """Unknown operation → ToolResult(success=False) with helpful message."""
    tool, uc = _make_tool()

    result = await tool.execute(operation="frobnicate")

    assert result.success is False
    assert "frobnicate" in result.output or "unknown" in result.output.lower()


@pytest.mark.asyncio
async def test_create_invalid_trigger_payload_is_error() -> None:
    """channel_send with missing required 'text' field → validation error."""
    tool, uc = _make_tool()

    result = await tool.execute(
        operation="create",
        name="Bad Payload",
        task_kind="one_shot",
        trigger_type="channel_send",
        trigger_payload={},  # missing 'text'
        schedule="2026-06-01T10:00:00Z",
    )

    assert result.success is False
    uc.create_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_missing_name_is_error() -> None:
    """create without name → validation error."""
    tool, uc = _make_tool()

    result = await tool.execute(
        operation="create",
        task_kind="one_shot",
        trigger_type="channel_send",
        trigger_payload={"text": "hi"},
        schedule="2026-06-01T10:00:00Z",
    )

    assert result.success is False
    assert "name" in result.output


@pytest.mark.asyncio
async def test_create_missing_schedule_is_error() -> None:
    """create without schedule → validation error."""
    tool, uc = _make_tool()

    result = await tool.execute(
        operation="create",
        name="Task",
        task_kind="one_shot",
        trigger_type="channel_send",
        trigger_payload={"text": "hi"},
    )

    assert result.success is False
    assert "schedule" in result.output


@pytest.mark.asyncio
async def test_create_invalid_task_kind_is_error() -> None:
    """create with unknown task_kind → validation error."""
    tool, uc = _make_tool()

    result = await tool.execute(
        operation="create",
        name="Task",
        task_kind="daily",
        trigger_type="channel_send",
        trigger_payload={"text": "hi"},
        schedule="2026-06-01T10:00:00Z",
    )

    assert result.success is False
    assert "task_kind" in result.output or "daily" in result.output


@pytest.mark.asyncio
async def test_create_invalid_trigger_type_is_error() -> None:
    """create with unknown trigger_type → validation error."""
    tool, uc = _make_tool()

    result = await tool.execute(
        operation="create",
        name="Task",
        task_kind="one_shot",
        trigger_type="webhook",
        trigger_payload={"url": "http://example.com"},
        schedule="2026-06-01T10:00:00Z",
    )

    assert result.success is False
    assert "trigger_type" in result.output or "webhook" in result.output


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_too_many_active_tasks_error() -> None:
    """TooManyActiveTasksError → ToolResult(success=False)."""
    tool, uc = _make_tool()
    uc.create_task.side_effect = TooManyActiveTasksError(agent_id=_AGENT_ID)

    result = await tool.execute(
        operation="create",
        name="Task",
        task_kind="one_shot",
        trigger_type="channel_send",
        trigger_payload={"text": "hi"},
        schedule="2026-06-01T10:00:00Z",
    )

    assert result.success is False
    assert _AGENT_ID in result.output or "21" in result.output


@pytest.mark.asyncio
async def test_create_unexpected_exception_returns_error() -> None:
    """Unexpected RuntimeError from use case → ToolResult(success=False) with generic message."""
    tool, uc = _make_tool()
    uc.create_task.side_effect = RuntimeError("DB connection lost")

    result = await tool.execute(
        operation="create",
        name="Task",
        task_kind="one_shot",
        trigger_type="channel_send",
        trigger_payload={"text": "hi"},
        schedule="2026-06-01T10:00:00Z",
    )

    assert result.success is False
    # Error message contains something about the exception
    assert "DB connection lost" in result.output or "error" in result.output.lower()


@pytest.mark.asyncio
async def test_list_unexpected_exception_returns_error() -> None:
    """Unexpected exception in list → ToolResult(success=False)."""
    tool, uc = _make_tool()
    uc.list_tasks.side_effect = RuntimeError("unexpected")

    result = await tool.execute(operation="list")

    assert result.success is False


@pytest.mark.asyncio
async def test_get_scheduler_error_returns_failure() -> None:
    """Generic SchedulerError in get → ToolResult(success=False)."""
    tool, uc = _make_tool()
    uc.get_task.side_effect = SchedulerError("Scheduler unavailable")

    result = await tool.execute(operation="get", task_id=5)

    assert result.success is False


@pytest.mark.asyncio
async def test_update_task_not_found_returns_failure() -> None:
    """TaskNotFoundError in update → ToolResult(success=False)."""
    tool, uc = _make_tool()
    uc.update_task.side_effect = TaskNotFoundError("Task 77 not found")

    result = await tool.execute(operation="update", task_id=77, name="new")

    assert result.success is False


@pytest.mark.asyncio
async def test_error_result_has_success_false_and_error_field() -> None:
    """_error helper → ToolResult with success=False and error field set."""
    tool, uc = _make_tool()
    uc.get_task.side_effect = TaskNotFoundError("not found")

    result = await tool.execute(operation="get", task_id=1)

    assert isinstance(result, ToolResult)
    assert result.success is False
    assert result.error is not None
    assert result.tool_name == "scheduler"


# ---------------------------------------------------------------------------
# LLM kind name mapping round-trip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_task_kind_llm_mapping_oneshot() -> None:
    """list maps domain 'oneshot' → LLM 'one_shot'."""
    tool, uc = _make_tool()
    task = _make_task(task_id=1, task_kind=TaskKind.ONESHOT)
    uc.list_tasks.return_value = [task]

    result = await tool.execute(operation="list")
    data = json.loads(result.output)
    assert data["tasks"][0]["task_kind"] == "one_shot"


@pytest.mark.asyncio
async def test_list_task_kind_llm_mapping_recurring() -> None:
    """list maps domain 'recurrent' → LLM 'recurring'."""
    tool, uc = _make_tool()
    task = _make_task(task_id=2, task_kind=TaskKind.RECURRENT)
    uc.list_tasks.return_value = [task]

    result = await tool.execute(operation="list")
    data = json.loads(result.output)
    assert data["tasks"][0]["task_kind"] == "recurring"


@pytest.mark.asyncio
async def test_create_maps_one_shot_to_domain_oneshot() -> None:
    """create with LLM 'one_shot' → domain TaskKind.ONESHOT."""
    tool, uc = _make_tool()
    created = _make_task(task_id=1, task_kind=TaskKind.ONESHOT)
    uc.create_task.return_value = created

    await tool.execute(
        operation="create",
        name="Task",
        task_kind="one_shot",
        trigger_type="channel_send",
        trigger_payload={"text": "t"},
        schedule="2026-06-01T10:00:00Z",
    )

    call_arg = uc.create_task.call_args[0][0]
    assert call_arg.task_kind == TaskKind.ONESHOT


@pytest.mark.asyncio
async def test_create_maps_recurring_to_domain_recurrent() -> None:
    """create with LLM 'recurring' → domain TaskKind.RECURRENT."""
    tool, uc = _make_tool()
    created = _make_task(task_id=2, task_kind=TaskKind.RECURRENT)
    uc.create_task.return_value = created

    await tool.execute(
        operation="create",
        name="Task",
        task_kind="recurring",
        trigger_type="channel_send",
        trigger_payload={"text": "t"},
        schedule="0 9 * * *",
    )

    call_arg = uc.create_task.call_args[0][0]
    assert call_arg.task_kind == TaskKind.RECURRENT


# ---------------------------------------------------------------------------
# T4: inyección de channel context en channel_send
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_channel_send_target_auto_inyectado_desde_contexto() -> None:
    """channel_send con contexto → target se inyecta desde context.routing_key."""
    ctx = ChannelContext(channel_type="telegram", user_id="999")
    tool, uc = _make_tool(get_channel_context=lambda: ctx)
    created = _make_task(task_id=10, name="Canal Task")
    uc.create_task.return_value = created

    result = await tool.execute(
        operation="create",
        name="Canal Task",
        task_kind="one_shot",
        trigger_type="channel_send",
        trigger_payload={"text": "mensaje programado"},
        schedule="2026-06-01T10:00:00Z",
    )

    assert result.success is True
    call_arg: ScheduledTask = uc.create_task.call_args[0][0]
    assert isinstance(call_arg.trigger_payload, ChannelSendPayload)
    assert call_arg.trigger_payload.target == "telegram:999"
    assert call_arg.trigger_payload.text == "mensaje programado"


@pytest.mark.asyncio
async def test_create_channel_send_user_id_override_reconstruye_target() -> None:
    """channel_send con user_id override → target usa channel_type del contexto + user_id del LLM."""
    ctx = ChannelContext(channel_type="telegram", user_id="999")
    tool, uc = _make_tool(get_channel_context=lambda: ctx)
    created = _make_task(task_id=11, name="Override Task")
    uc.create_task.return_value = created

    result = await tool.execute(
        operation="create",
        name="Override Task",
        task_kind="one_shot",
        trigger_type="channel_send",
        trigger_payload={"text": "para otro usuario", "user_id": "777"},
        schedule="2026-06-01T10:00:00Z",
    )

    assert result.success is True
    call_arg: ScheduledTask = uc.create_task.call_args[0][0]
    assert isinstance(call_arg.trigger_payload, ChannelSendPayload)
    # target reconstruido con channel_type del contexto + user_id del LLM
    assert call_arg.trigger_payload.target == "telegram:777"
    assert call_arg.trigger_payload.user_id == "777"


@pytest.mark.asyncio
async def test_create_channel_send_sin_contexto_retorna_error() -> None:
    """channel_send sin contexto de canal → error descriptivo."""
    tool, uc = _make_tool(get_channel_context=lambda: None)

    result = await tool.execute(
        operation="create",
        name="Sin Contexto",
        task_kind="one_shot",
        trigger_type="channel_send",
        trigger_payload={"text": "hola"},
        schedule="2026-06-01T10:00:00Z",
    )

    assert result.success is False
    assert "contexto" in result.output.lower() or "canal" in result.output.lower()
    uc.create_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_no_channel_send_sin_inyeccion() -> None:
    """trigger no channel_send → no se usa get_channel_context (comportamiento existente)."""
    # Contexto que falla si se llama → no debe llamarse para agent_send
    def ctx_falla():
        raise RuntimeError("get_channel_context no debe llamarse para agent_send")

    tool, uc = _make_tool(get_channel_context=ctx_falla)
    task = _make_task(task_id=12, trigger_type=TriggerType.AGENT_SEND)
    task = task.model_copy(update={
        "trigger_payload": AgentSendPayload(agent_id="otro-agent", task="hacer algo")
    })
    uc.create_task.return_value = task

    result = await tool.execute(
        operation="create",
        name="Agent Task",
        task_kind="one_shot",
        trigger_type="agent_send",
        trigger_payload={"agent_id": "otro-agent", "task": "hacer algo"},
        schedule="2026-06-01T10:00:00Z",
    )

    assert result.success is True


@pytest.mark.asyncio
async def test_create_channel_send_target_en_payload_descartado_silenciosamente() -> None:
    """LLM envía 'target' en payload → descartado silenciosamente; se usa el del contexto."""
    ctx = ChannelContext(channel_type="telegram", user_id="123")
    tool, uc = _make_tool(get_channel_context=lambda: ctx)
    created = _make_task(task_id=13, name="Target Strip")
    uc.create_task.return_value = created

    result = await tool.execute(
        operation="create",
        name="Target Strip",
        task_kind="one_shot",
        trigger_type="channel_send",
        trigger_payload={"text": "test", "target": "hacker:000"},  # debe ignorarse
        schedule="2026-06-01T10:00:00Z",
    )

    assert result.success is True
    call_arg: ScheduledTask = uc.create_task.call_args[0][0]
    # target debe venir del contexto, no del LLM
    assert call_arg.trigger_payload.target == "telegram:123"


# ---------------------------------------------------------------------------
# update — inyección de channel context para channel_send (verify fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_channel_send_inyecta_target_desde_contexto() -> None:
    """update trigger_payload de channel_send → target inyectado desde contexto."""
    tool, uc = _make_tool()
    existing = _make_task(task_id=200, trigger_type=TriggerType.CHANNEL_SEND)
    existing.trigger_payload = ChannelSendPayload(target="telegram:old_user", text="viejo")
    uc.get_task.return_value = existing
    updated = _make_task(task_id=200)
    uc.update_task.return_value = updated

    result = await tool.execute(
        operation="update",
        task_id=200,
        trigger_payload={"text": "nuevo texto"},
    )

    assert result.success is True
    kwargs = uc.update_task.call_args[1]
    payload = kwargs["trigger_payload"]
    # target debe conservar el existente (no el del LLM, que no lo mandó)
    assert payload.target == "telegram:old_user"
    assert payload.text == "nuevo texto"


@pytest.mark.asyncio
async def test_update_channel_send_user_id_override() -> None:
    """update channel_send con user_id → target reconstruido con channel_type del contexto."""
    tool, uc = _make_tool()
    existing = _make_task(task_id=201, trigger_type=TriggerType.CHANNEL_SEND)
    existing.trigger_payload = ChannelSendPayload(target="telegram:old_user", text="viejo")
    uc.get_task.return_value = existing
    updated = _make_task(task_id=201)
    uc.update_task.return_value = updated

    result = await tool.execute(
        operation="update",
        task_id=201,
        trigger_payload={"text": "hola", "user_id": "999888"},
    )

    assert result.success is True
    kwargs = uc.update_task.call_args[1]
    payload = kwargs["trigger_payload"]
    assert payload.target == "telegram:999888"


@pytest.mark.asyncio
async def test_update_channel_send_sin_contexto_error() -> None:
    """update channel_send sin contexto de canal → error descriptivo."""
    tool, uc = _make_tool(get_channel_context=lambda: None)
    existing = _make_task(task_id=202, trigger_type=TriggerType.CHANNEL_SEND)
    uc.get_task.return_value = existing

    result = await tool.execute(
        operation="update",
        task_id=202,
        trigger_payload={"text": "texto"},
    )

    assert result.success is False
    assert "contexto de canal" in result.output.lower()


# ---------------------------------------------------------------------------
# Echo autoconfirmable en create/update
#
# Regresión: el output devuelto al LLM tras `create`/`update` solo incluía
# `{id, name}`. Sin echo de `schedule`, `next_run_at` ni `task_status`, algunos
# LLMs interpretaban el resultado como ambiguo y reintentaban la operación,
# produciendo tareas duplicadas. El echo completo es el único contrato estable
# porque `_tool_loop` propaga SOLO `result.output` al LLM (el flag `success` del
# envelope no se ve).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_echo_includes_all_confirm_fields() -> None:
    """`create` debe devolver flag booleano + echo de schedule/next_run_at/task_status."""
    tool, uc = _make_tool()
    created = _make_task(task_id=100, name="Echo Task", task_kind=TaskKind.RECURRENT)
    created = created.model_copy(update={
        "schedule": "0 8 * * *",
        "next_run": datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone.utc),
        "status": TaskStatus.PENDING,
    })
    uc.create_task.return_value = created

    result = await tool.execute(
        operation="create",
        name="Echo Task",
        task_kind="recurring",
        trigger_type="channel_send",
        trigger_payload={"text": "daily"},
        schedule="0 8 * * *",
    )

    assert result.success is True
    data = json.loads(result.output)
    # Flag booleano explícito (paralelo a `deleted=True` de _delete)
    assert data["created"] is True
    # Echo autoritativo post-persistencia
    assert data["id"] == 100
    assert data["name"] == "Echo Task"
    assert data["task_kind"] == "recurring"
    assert data["trigger_type"] == "channel_send"
    assert data["schedule"] == "0 8 * * *"
    assert data["next_run_at"] == "2026-06-01T08:00:00+00:00"
    assert data["task_status"] == "pending"


@pytest.mark.asyncio
async def test_create_echo_next_run_at_null_when_repo_cant_resolve() -> None:
    """Si el repo no pudo resolver next_run, el echo lo refleja como null — no lo omite."""
    tool, uc = _make_tool()
    created = _make_task(task_id=101, name="Sin Next Run")
    created = created.model_copy(update={"next_run": None})
    uc.create_task.return_value = created

    result = await tool.execute(
        operation="create",
        name="Sin Next Run",
        task_kind="one_shot",
        trigger_type="channel_send",
        trigger_payload={"text": "x"},
        schedule="2026-06-01T10:00:00Z",
    )

    assert result.success is True
    data = json.loads(result.output)
    assert data["created"] is True
    assert data["next_run_at"] is None


@pytest.mark.asyncio
async def test_update_echo_reflects_runtime_reset() -> None:
    """
    Tras un edit invalidante, el use case resetea status/retry/next_run.
    El echo debe reflejar el estado POST-reset para que el LLM vea sin
    ambigüedad que la task salió del modo zombie.
    """
    tool, uc = _make_tool()
    # Simulamos la task ya con el reset aplicado por ScheduleTaskUseCase.update_task.
    # El mock devuelve lo que queremos ver echo'ado, independiente del schedule
    # que el caller le haya pasado al tool.
    post_reset = _make_task(task_id=200, name="Post Reset")
    post_reset = post_reset.model_copy(update={
        "schedule": "2026-06-01T09:00:00+00:00",
        "status": TaskStatus.PENDING,  # reset desde FAILED
        "next_run": datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc),
    })
    uc.update_task.return_value = post_reset

    result = await tool.execute(
        operation="update",
        task_id=200,
        schedule="2026-06-01T09:00:00Z",
    )

    assert result.success is True
    data = json.loads(result.output)
    assert data["updated"] is True
    assert data["id"] == 200
    assert data["schedule"] == "2026-06-01T09:00:00+00:00"
    assert data["task_status"] == "pending"
    assert data["next_run_at"] == "2026-06-01T09:00:00+00:00"


@pytest.mark.asyncio
async def test_update_echo_preserves_disabled_status() -> None:
    """
    Si la task estaba disabled, el use case la mantiene disabled aun tras
    edit invalidante. El echo debe mostrarlo: es la señal de que el LLM NO
    debe reintentar un enable implícito.
    """
    tool, uc = _make_tool()
    post_update = _make_task(task_id=201, name="Sigue Disabled")
    post_update = post_update.model_copy(update={"status": TaskStatus.DISABLED})
    uc.update_task.return_value = post_update

    result = await tool.execute(
        operation="update",
        task_id=201,
        schedule="2099-01-01T00:00:00Z",
    )

    assert result.success is True
    data = json.loads(result.output)
    assert data["updated"] is True
    assert data["task_status"] == "disabled"
