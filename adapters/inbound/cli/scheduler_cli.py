"""
Scheduler CLI sub-app.

Expone `inaki scheduler <cmd>` para gestionar tareas programadas a través de
la interfaz `ISchedulerUseCase` (ScheduleTaskUseCase). Montado en main.py via:

    app.add_typer(scheduler_app, name="scheduler", help="Manage scheduled tasks")

Comandos:
  list     — listar todas las tareas (o filtrar habilitadas)
  show     — mostrar una tarea por ID
  edit     — editar una tarea en $EDITOR (YAML round-trip con validación Pydantic)
  enable   — habilitar una tarea
  disable  — deshabilitar una tarea
  rm       — eliminar una tarea (protegidas id < 100 son rechazadas)
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import typer
import yaml
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from core.domain.entities.task import ScheduledTask
from core.domain.errors import BuiltinTaskProtectedError, TaskNotFoundError

if TYPE_CHECKING:
    from core.ports.inbound.scheduler_port import ISchedulerUseCase

# ---------------------------------------------------------------------------
# Sub-app
# ---------------------------------------------------------------------------

scheduler_app = typer.Typer(
    help="Manage scheduled tasks.",
    no_args_is_help=True,
)

# ---------------------------------------------------------------------------
# Editable fields allowlist — runtime-managed fields are excluded from the editor
# ---------------------------------------------------------------------------

_EDITABLE_FIELDS: set[str] = {
    "name",
    "description",
    "task_kind",
    "trigger_type",
    "trigger_payload",
    "schedule",
    "enabled",
    "executions_remaining",
    "log_enabled",
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _bootstrap_uc(ctx: typer.Context) -> "ISchedulerUseCase":
    """Resolve dirs from ctx.obj, build AppContainer, return schedule_task_uc."""
    import sys

    from main import _bootstrap, _resolve_dirs
    from infrastructure.container import AppContainer

    config_dir_override: Optional[Path] = ctx.obj.get("config_dir") if ctx.obj else None
    config_dir, agents_dir = _resolve_dirs(config_dir_override)

    try:
        global_config, registry = _bootstrap(config_dir, agents_dir)
    except SystemExit:
        raise

    container = AppContainer(global_config, registry)
    return container.schedule_task_uc


def _run_async(coro: Any) -> Any:
    """Run an async coroutine in the current thread."""
    return asyncio.run(coro)


def _render_table(tasks: list[ScheduledTask]) -> None:
    """Render tasks as a rich table to stdout."""
    table = Table(title=None, show_lines=False)
    for col in ("ID", "Name", "Kind", "Trigger", "Enabled", "Next run"):
        table.add_column(col)
    for t in tasks:
        table.add_row(
            str(t.id),
            t.name,
            t.task_kind.value,
            t.trigger_type.value,
            "yes" if t.enabled else "no",
            t.next_run.isoformat() if t.next_run else "-",
        )
    Console().print(table)


def _render_json(payload: Any) -> None:
    """Dump payload to stdout as indented JSON."""
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))


def _dump_editable(task: ScheduledTask) -> str:
    """Return a YAML string with only the editable fields, prefixed by a header comment."""
    header = (
        f"# Editing task {task.id} — save and exit your editor to apply.\n"
        f"# Runtime fields (id, status, next_run, last_run, created_at, retry_count)"
        f" are managed by the scheduler and are not shown here.\n"
        f"# Discriminated union tip: if you change trigger_type, also update"
        f" trigger_payload.type to match.\n"
    )
    data = task.model_dump(mode="json", include=_EDITABLE_FIELDS)
    return header + yaml.safe_dump(
        data,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )


def _edit_yaml_loop(
    initial_yaml: str,
    existing_task: ScheduledTask,
) -> Optional[dict[str, Any]]:
    """
    Write initial_yaml to a tempfile, open $EDITOR in a loop (max 3 attempts),
    parse + validate, and return a dict of edited fields (only keys that were
    present in the YAML).

    Returns None if the file was unchanged.
    Raises typer.Exit on abort, empty file, or exhausted attempts.
    """
    # Write tempfile
    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            delete=False,
            encoding="utf-8",
        ) as fh:
            fh.write(initial_yaml)
            tmp_path = fh.name

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            editor = os.environ.get("EDITOR", "vi")
            rc = subprocess.call([editor, tmp_path])

            if rc != 0:
                typer.echo("Aborted.", err=True)
                raise typer.Exit(code=1)

            with open(tmp_path, "r", encoding="utf-8") as fh:
                text = fh.read()

            if not text.strip():
                typer.echo("Error: empty document — aborted.", err=True)
                raise typer.Exit(code=1)

            # Attempt to parse YAML
            try:
                parsed = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                typer.echo(f"YAML parse error (attempt {attempt}/{max_attempts}): {exc}", err=True)
                if attempt < max_attempts:
                    typer.echo("Re-opening editor...", err=True)
                    continue
                typer.echo("Error: giving up after 3 failed attempts.", err=True)
                raise typer.Exit(code=2)

            if not isinstance(parsed, dict):
                typer.echo(
                    f"Error: expected a YAML mapping, got {type(parsed).__name__} "
                    f"(attempt {attempt}/{max_attempts})",
                    err=True,
                )
                if attempt < max_attempts:
                    typer.echo("Re-opening editor...", err=True)
                    continue
                typer.echo("Error: giving up after 3 failed attempts.", err=True)
                raise typer.Exit(code=2)

            # Merge parsed values on top of existing runtime state, then validate
            merged = {**existing_task.model_dump(), **parsed}
            try:
                validated = ScheduledTask.model_validate(merged)
            except ValidationError as exc:
                flat_errors = "; ".join(
                    f"{'.'.join(str(l) for l in e['loc'])}: {e['msg']}"
                    for e in exc.errors()
                )
                typer.echo(
                    f"Validation error (attempt {attempt}/{max_attempts}): {flat_errors}",
                    err=True,
                )
                if attempt < max_attempts:
                    typer.echo("Re-opening editor...", err=True)
                    continue
                typer.echo("Error: giving up after 3 failed attempts.", err=True)
                raise typer.Exit(code=2)

            # Build dict of only the keys that were explicitly present in parsed YAML
            edited_fields: dict[str, Any] = {
                k: getattr(validated, k)
                for k in _EDITABLE_FIELDS
                if k in parsed
            }

            # Check for actual differences
            original = existing_task.model_dump(mode="json", include=_EDITABLE_FIELDS)
            new_values = validated.model_dump(mode="json", include=set(edited_fields.keys()))
            if not any(original.get(k) != new_values.get(k) for k in edited_fields):
                return None

            return edited_fields

        # Should not be reachable (loop exhausts via Exit), but be explicit
        typer.echo("Error: giving up after 3 failed attempts.", err=True)
        raise typer.Exit(code=2)

    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@scheduler_app.command("list")
def list_cmd(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    enabled_only: bool = typer.Option(False, "--enabled-only", help="Show only enabled tasks"),
) -> None:
    """List scheduled tasks."""
    uc = _bootstrap_uc(ctx)
    try:
        tasks = _run_async(uc.list_tasks())
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    if enabled_only:
        tasks = [t for t in tasks if t.enabled]

    if not tasks:
        typer.echo("No scheduled tasks.")
        raise typer.Exit(code=0)

    if json_output:
        _render_json([t.model_dump(mode="json") for t in tasks])
    else:
        _render_table(tasks)


@scheduler_app.command("show")
def show_cmd(
    ctx: typer.Context,
    task_id: int = typer.Argument(..., metavar="ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show details of a scheduled task."""
    uc = _bootstrap_uc(ctx)
    try:
        task = _run_async(uc.get_task(task_id))
    except TaskNotFoundError:
        typer.echo(f"Error: task {task_id} not found", err=True)
        raise typer.Exit(code=1)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    if json_output:
        _render_json(task.model_dump(mode="json"))
    else:
        typer.echo(f"=== Task {task.id} ===")
        typer.echo(
            yaml.safe_dump(task.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
            nl=False,
        )


@scheduler_app.command("edit")
def edit_cmd(
    ctx: typer.Context,
    task_id: int = typer.Argument(..., metavar="ID"),
) -> None:
    """Edit a scheduled task in $EDITOR (YAML round-trip)."""
    uc = _bootstrap_uc(ctx)
    try:
        task = _run_async(uc.get_task(task_id))
    except TaskNotFoundError:
        typer.echo(f"Error: task {task_id} not found", err=True)
        raise typer.Exit(code=1)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    initial_yaml = _dump_editable(task)
    edited_fields = _edit_yaml_loop(initial_yaml, task)

    if edited_fields is None:
        typer.echo("No changes.")
        raise typer.Exit(code=0)

    try:
        _run_async(uc.update_task(task_id, **edited_fields))
    except BuiltinTaskProtectedError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    except ValidationError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Task {task_id} updated.")


@scheduler_app.command("enable")
def enable_cmd(
    ctx: typer.Context,
    task_id: int = typer.Argument(..., metavar="ID"),
) -> None:
    """Enable a scheduled task."""
    uc = _bootstrap_uc(ctx)
    try:
        _run_async(uc.enable_task(task_id))
    except TaskNotFoundError:
        typer.echo(f"Error: task {task_id} not found", err=True)
        raise typer.Exit(code=1)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Task {task_id} enabled.")


@scheduler_app.command("disable")
def disable_cmd(
    ctx: typer.Context,
    task_id: int = typer.Argument(..., metavar="ID"),
) -> None:
    """Disable a scheduled task."""
    uc = _bootstrap_uc(ctx)
    try:
        _run_async(uc.disable_task(task_id))
    except TaskNotFoundError:
        typer.echo(f"Error: task {task_id} not found", err=True)
        raise typer.Exit(code=1)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Task {task_id} disabled.")


@scheduler_app.command("rm")
def rm_cmd(
    ctx: typer.Context,
    task_id: int = typer.Argument(..., metavar="ID"),
) -> None:
    """Remove a scheduled task (builtin tasks with id < 100 are protected)."""
    uc = _bootstrap_uc(ctx)
    try:
        _run_async(uc.delete_task(task_id))
    except BuiltinTaskProtectedError:
        typer.echo(f"Error: task {task_id} is a builtin and cannot be deleted", err=True)
        raise typer.Exit(code=1)
    except TaskNotFoundError:
        typer.echo(f"Error: task {task_id} not found", err=True)
        raise typer.Exit(code=1)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Task {task_id} deleted.")
