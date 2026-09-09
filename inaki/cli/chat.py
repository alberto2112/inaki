"""Comando ``chat`` y el chat por defecto (``inaki`` a secas) vía daemon HTTP."""

from __future__ import annotations

import sys
from typing import Optional

import typer

from inaki.cli import _common


def run_chat(client, agent_id: str) -> None:
    """Chat interactivo via daemon HTTP — sin AppContainer."""
    from inaki.channels.cli.runner import run_cli

    run_cli(client, agent_id)


def run_task(
    client,
    agent_id: str,
    task: str,
    channel: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> None:
    """Ejecuta una tarea oneshot y vuelca el resultado a stdout."""
    from rich.console import Console

    spinner = Console(stderr=True)
    with spinner.status("Ejecutando...", spinner="dots"):
        turn_result = client.task_turn(agent_id, task, channel=channel, chat_id=chat_id)
    for intermediate in turn_result.intermediates:
        print(intermediate)
    print(turn_result.reply)


def invoke_task(
    remote_url: Optional[str],
    remote_key: Optional[str],
    agent: Optional[str],
    task: str,
    channel: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> None:
    """Conecta al daemon, ejecuta la tarea y sale.

    Si ``channel`` y ``chat_id`` se pasan, el daemon carga el historial de ese
    scope. Both-or-none: si solo viene uno, el daemon responde 422.
    """
    if (channel is None) != (chat_id is None):
        print(
            "--channel y --chat-id deben usarse juntos (o ninguno).",
            file=sys.stderr,
        )
        raise typer.Exit(code=2)

    config_dir, _ = _common.resolve_dirs()
    client, global_config = _common.build_daemon_client(config_dir, remote_url, remote_key)
    _common.require_daemon(client)
    agent_id = agent or global_config.app.default_agent
    _common.handle_daemon_errors(
        lambda: run_task(client, agent_id, task, channel=channel, chat_id=chat_id)
    )


def invoke_default_chat(
    remote_url: Optional[str],
    remote_key: Optional[str],
) -> None:
    """Lanza el chat interactivo con el agente por defecto via daemon HTTP."""
    config_dir, _ = _common.resolve_dirs()
    client, global_config = _common.build_daemon_client(config_dir, remote_url, remote_key)
    _common.require_daemon(client)
    agent_id = global_config.app.default_agent
    run_chat(client, agent_id)


def chat(
    ctx: typer.Context,
    agent: Optional[str] = typer.Option(
        None,
        "--agent",
        metavar="AGENT_ID|list",
        help="ID del agente o 'list' para listar agentes disponibles",
    ),
    task: Optional[str] = typer.Option(
        None,
        "--task",
        metavar="TASK",
        help="Ejecuta una tarea oneshot y devuelve el resultado por stdout (sin persistir historial).",
    ),
    channel: Optional[str] = typer.Option(
        None,
        "--channel",
        metavar="CHANNEL",
        help=(
            "Canal del scope de historial a cargar para --task (ej. 'telegram'). "
            "Both-or-none con --chat-id."
        ),
    ),
    chat_id: Optional[str] = typer.Option(
        None,
        "--chat-id",
        metavar="CHAT_ID",
        help=(
            "ID del chat dentro del canal (ej. id de grupo de Telegram). "
            "Para IDs negativos usar la forma `--chat-id=-1001582404077` (con '='), "
            "porque Click confunde el '-' inicial con una flag corta."
        ),
    ),
) -> None:
    """Chat interactivo con un agente via daemon HTTP."""
    remote_url: Optional[str] = ctx.obj.get("remote_url") if ctx.obj else None
    remote_key: Optional[str] = ctx.obj.get("remote_key") if ctx.obj else None
    config_dir, _ = _common.resolve_dirs()

    client, global_config = _common.build_daemon_client(config_dir, remote_url, remote_key)
    _common.require_daemon(client)
    agent_id = agent or global_config.app.default_agent

    if task:
        if (channel is None) != (chat_id is None):
            print(
                "--channel y --chat-id deben usarse juntos (o ninguno).",
                file=sys.stderr,
            )
            raise typer.Exit(code=2)
        _common.handle_daemon_errors(
            lambda: run_task(client, agent_id, task, channel=channel, chat_id=chat_id)
        )
    else:
        run_chat(client, agent_id)
