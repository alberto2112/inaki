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
from core.ports.outbound.tool_port import ToolResult


# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------

_AGENT_ID = "test-agent"
_USER_TZ = "UTC"


def _make_tool(
    agent_id: str = _AGENT_ID,
    user_timezone: str = _USER_TZ,
    uc: MagicMock | None = None,
) -> tuple[SchedulerTool, MagicMock]:
    """Returns (tool, mock_uc). mock_uc has all methods as AsyncMock by default."""
    if uc is None:
        uc = MagicMock()
        uc.create_task = AsyncMock()
        uc.list_tasks = AsyncMock()
        uc.get_task = AsyncMock()
        uc.update_task = AsyncMock()
        uc.delete_task = AsyncMock()
    tool = SchedulerTool(
        schedule_task_uc=uc,
        agent_id=agent_id,
        user_timezone=user_timezone,
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
        trigger_payload=ChannelSendPayload(channel_id="ch1", text="hello"),
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
        trigger_payload={"channel_id": "ch1", "text": "hello"},
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
        trigger_payload={"channel_id": "ch1", "text": "ping"},
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
        trigger_payload={"channel_id": "ch1", "text": "daily"},
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
        "trigger_payload": AgentSendPayload(agent_id="other-agent")
    })
    uc.create_task.return_value = task

    result = await tool.execute(
        operation="create",
        name="Agent Task",
        task_kind="one_shot",
        trigger_type="agent_send",
        trigger_payload={"agent_id": "other-agent"},
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
        trigger_payload={"channel_id": "ch1", "text": "hi"},
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
        trigger_payload={"channel_id": "ch1", "text": "hello"},
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
        trigger_payload={"channel_id": "ch1", "text": "hi"},
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
        trigger_payload={"channel_id": "ch1"},  # missing 'text'
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
        trigger_payload={"channel_id": "ch1", "text": "hi"},
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
        trigger_payload={"channel_id": "ch1", "text": "hi"},
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
        trigger_payload={"channel_id": "ch1", "text": "hi"},
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
        trigger_payload={"channel_id": "ch1", "text": "hi"},
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
        trigger_payload={"channel_id": "ch1", "text": "hi"},
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
        trigger_payload={"channel_id": "c", "text": "t"},
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
        trigger_payload={"channel_id": "c", "text": "t"},
        schedule="0 9 * * *",
    )

    call_arg = uc.create_task.call_args[0][0]
    assert call_arg.task_kind == TaskKind.RECURRENT
