"""
Entry point de Iñaki.

Modos de uso:
  inaki                                    → CLI interactivo (agente por defecto)
  inaki chat --agent dev                   → CLI con agente específico
  inaki chat --agent list                  → listar agentes disponibles
  inaki inspect "mensaje"                  → inspeccionar pipeline RAG sin llamar al LLM
  inaki consolidate                        → consolida TODOS los agentes habilitados (con delay)
  inaki consolidate --agent dev            → consolida solo el agente indicado
  inaki daemon                             → servicio systemd (todos los canales de todos los agentes)
  inaki reload                             → reinicia el daemon (cierra canales, recarga config y vuelve a levantar)
  inaki --config /etc/inaki/config daemon  → daemon con config custom
  inaki --remote http://raspi.local:6497   → conectarse a un daemon remoto (env: INAKI_REMOTE)
  inaki --remote URL --remote-key KEY chat → conectarse a daemon remoto con auth key explícita
  inaki setup                              → TUI interactiva de configuración (offline)
  inaki setup tui                          → ídem
  inaki setup secret-key                   → wizard Fernet legacy (INAKI_SECRET_KEY)
  inaki setup webui                        → placeholder (no disponible aún)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Optional

import typer

from adapters.inbound.cli.knowledge_cli import knowledge_app
from adapters.inbound.cli.scheduler_cli import scheduler_app
from adapters.inbound.cli.setup_cli import setup_app
from inaki import __version__

app = typer.Typer(
    name="inaki",
    help="Iñaki — asistente personal agentico",
    invoke_without_command=True,
    no_args_is_help=False,
)
def _version_callback(value: bool) -> None:
    if value:
        print(f"inaki {__version__}")
        raise typer.Exit()


app.add_typer(scheduler_app, name="scheduler", help="Manage scheduled tasks")
app.add_typer(knowledge_app, name="knowledge", help="Manage document knowledge sources")
app.add_typer(setup_app, name="setup", help="Configuración del sistema (TUI offline)")


def _get_config_dir() -> Path:
    return Path.home() / ".inaki" / "config"


def _get_agents_dir() -> Path:
    return Path.home() / ".inaki" / "agents"


def _bootstrap(config_dir: Path, agents_dir: Path):
    """Carga config, logging y registry. Retorna (global_config, registry)."""
    from infrastructure.config import load_global_config, AgentRegistry
    from infrastructure.logging_setup import setup_logging

    try:
        global_config, global_raw = load_global_config(config_dir)
    except Exception as exc:
        print(f"Error cargando configuración desde {config_dir}: {exc}", file=sys.stderr)
        sys.exit(1)

    setup_logging(global_config.app.log_level)

    registry = AgentRegistry(agents_dir, global_raw)
    if not registry.list_all():
        print(
            f"No hay agentes configurados en {agents_dir}. "
            "Crea un archivo .yaml de agente para comenzar.",
            file=sys.stderr,
        )
        sys.exit(1)

    return global_config, registry


def _run_daemon(config_dir: Path, agents_dir: Path, global_config, registry) -> None:
    """Arranca todos los canales en modo servicio systemd.

    Recibe paths + el bootstrap inicial ya hecho. El primer arranque usa el initial
    (sin re-leer config), y cada reload re-invoca ``_bootstrap`` desde cero leyendo
    el contenido actual de ``config_dir`` / ``agents_dir``.
    """
    import logging
    from infrastructure.container import AppContainer
    from adapters.inbound.daemon.runner import run_daemon

    logger = logging.getLogger(__name__)
    logger.info("Iniciando Iñaki en modo daemon")

    initial_container = AppContainer(global_config, registry)

    def bootstrap_fn():
        gc, reg = _bootstrap(config_dir, agents_dir)
        return AppContainer(gc, reg), reg

    asyncio.run(run_daemon(bootstrap_fn, initial=(initial_container, registry)))


def _run_cli(client, agent_id: str) -> None:
    """Chat interactivo via daemon HTTP — sin AppContainer."""
    from adapters.inbound.cli.cli_runner import run_cli

    run_cli(client, agent_id)


def _resolve_dirs(config_dir_override: Optional[Path]):
    """Resuelve config_dir y agents_dir, aplicando ensure_user_config si es necesario."""
    if config_dir_override:
        config_dir = config_dir_override
        agents_dir = config_dir / "agents"
    else:
        from infrastructure.config import ensure_user_config

        config_dir = _get_config_dir()
        agents_dir = _get_agents_dir()
        ensure_user_config(config_dir, agents_dir)
    return config_dir, agents_dir


def _build_daemon_client(
    config_dir: Path,
    remote_url: Optional[str] = None,
    remote_key: Optional[str] = None,
):
    """Construye DaemonClient con bootstrap mínimo — solo parsea YAML, sin AppContainer.

    Si `remote_url` está definido, apunta al daemon remoto en vez del local.
    El auth key se resuelve: `remote_key` > `admin.auth_key` del config local.
    """
    from infrastructure.config import load_global_config
    from adapters.outbound.daemon_client import DaemonClient

    global_config, _ = load_global_config(config_dir)
    admin = global_config.admin
    if remote_url:
        base_url = remote_url.rstrip("/")
        auth_key = remote_key if remote_key is not None else admin.auth_key
    else:
        base_url = f"http://{admin.host}:{admin.port}"
        auth_key = admin.auth_key
    client = DaemonClient(
        admin_base_url=base_url,
        auth_key=auth_key,
        chat_timeout=admin.chat_timeout,
    )
    return client, global_config


def _handle_daemon_errors(fn):
    """Ejecuta `fn` y mapea errores del daemon a mensajes limpios + typer.Exit(1)."""
    from core.domain.errors import (
        DaemonAuthError,
        DaemonClientError,
        DaemonNotRunningError,
        DaemonTimeoutError,
        UnknownAgentError,
    )

    try:
        return fn()
    except DaemonNotRunningError as exc:
        print(str(exc), file=sys.stderr)
    except UnknownAgentError as exc:
        print(f"Error: {exc}", file=sys.stderr)
    except DaemonAuthError as exc:
        print(f"Error de autenticación con el daemon: {exc}", file=sys.stderr)
    except DaemonTimeoutError as exc:
        print(f"Timeout al contactar al daemon: {exc}", file=sys.stderr)
    except DaemonClientError as exc:
        print(f"Error del daemon: {exc}", file=sys.stderr)
    raise typer.Exit(code=1)


def _require_daemon(client) -> None:
    """Verifica que el daemon esté corriendo. Sale con error si no."""
    if not client.health():
        print(
            "El daemon no está corriendo. Iniciá con `inaki daemon` o `systemctl start inaki`.",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)


def _run_task(
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


def _invoke_task(
    config_dir_override: Optional[Path],
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

    config_dir, _ = _resolve_dirs(config_dir_override)
    client, global_config = _build_daemon_client(config_dir, remote_url, remote_key)
    _require_daemon(client)
    agent_id = agent or global_config.app.default_agent
    _handle_daemon_errors(
        lambda: _run_task(client, agent_id, task, channel=channel, chat_id=chat_id)
    )


def _invoke_default_chat(
    config_dir_override: Optional[Path],
    remote_url: Optional[str],
    remote_key: Optional[str],
) -> None:
    """Lanza el chat interactivo con el agente por defecto via daemon HTTP."""
    config_dir, _ = _resolve_dirs(config_dir_override)
    client, global_config = _build_daemon_client(config_dir, remote_url, remote_key)
    _require_daemon(client)
    agent_id = global_config.app.default_agent
    _run_cli(client, agent_id)


@app.callback()
def _root(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Muestra la versión y sale.",
    ),
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        metavar="DIR",
        help="Directorio de configuración (default: ~/.inaki/config)",
    ),
    remote: Optional[str] = typer.Option(
        None,
        "--remote",
        metavar="URL",
        envvar="INAKI_REMOTE",
        help="URL del admin server de un daemon remoto (ej: http://raspi.local:6497). "
        "Si se omite, usa el daemon local configurado en admin.host/port.",
    ),
    remote_key: Optional[str] = typer.Option(
        None,
        "--remote-key",
        metavar="KEY",
        envvar="INAKI_REMOTE_KEY",
        help="Auth key del daemon remoto. Si se omite, usa admin.auth_key del config local.",
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
    """Iñaki — asistente personal agentico."""
    ctx.ensure_object(dict)
    ctx.obj["config_dir"] = config
    ctx.obj["remote_url"] = remote
    ctx.obj["remote_key"] = remote_key
    if ctx.invoked_subcommand is None:
        if task:
            _invoke_task(config, remote, remote_key, None, task, channel=channel, chat_id=chat_id)
        else:
            _invoke_default_chat(config, remote, remote_key)


@app.command()
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
    config_dir_override: Optional[Path] = ctx.obj.get("config_dir") if ctx.obj else None
    remote_url: Optional[str] = ctx.obj.get("remote_url") if ctx.obj else None
    remote_key: Optional[str] = ctx.obj.get("remote_key") if ctx.obj else None
    config_dir, _ = _resolve_dirs(config_dir_override)

    client, global_config = _build_daemon_client(config_dir, remote_url, remote_key)
    _require_daemon(client)
    agent_id = agent or global_config.app.default_agent

    if task:
        if (channel is None) != (chat_id is None):
            print(
                "--channel y --chat-id deben usarse juntos (o ninguno).",
                file=sys.stderr,
            )
            raise typer.Exit(code=2)
        _handle_daemon_errors(
            lambda: _run_task(client, agent_id, task, channel=channel, chat_id=chat_id)
        )
    else:
        _run_cli(client, agent_id)


@app.command()
def daemon(
    ctx: typer.Context,
) -> None:
    """Arranca como servicio systemd (levanta todos los canales de todos los agentes)."""
    config_dir_override: Optional[Path] = ctx.obj.get("config_dir") if ctx.obj else None
    config_dir, agents_dir = _resolve_dirs(config_dir_override)
    global_config, registry = _bootstrap(config_dir, agents_dir)
    _run_daemon(config_dir, agents_dir, global_config, registry)


@app.command()
def inspect(
    ctx: typer.Context,
    message: str = typer.Argument(
        ...,
        metavar="MESSAGE",
        help="Mensaje para inspeccionar el pipeline RAG (sin llamar al LLM)",
    ),
    agent: Optional[str] = typer.Option(
        None,
        "--agent",
        metavar="AGENT_ID",
        help="ID del agente (default: agente por defecto del global)",
    ),
) -> None:
    """Inspecciona el pipeline RAG para un mensaje sin llamar al LLM."""
    config_dir_override: Optional[Path] = ctx.obj.get("config_dir") if ctx.obj else None
    remote_url: Optional[str] = ctx.obj.get("remote_url") if ctx.obj else None
    remote_key: Optional[str] = ctx.obj.get("remote_key") if ctx.obj else None
    config_dir, _ = _resolve_dirs(config_dir_override)

    client, global_config = _build_daemon_client(config_dir, remote_url, remote_key)
    _require_daemon(client)
    agent_id = agent or global_config.app.default_agent
    import json

    result = _handle_daemon_errors(lambda: client.inspect(agent_id, message))
    print(json.dumps(result, indent=2, ensure_ascii=False))


@app.command()
def reload(
    ctx: typer.Context,
) -> None:
    """Reinicia el daemon: cierra todos los channels, recarga config y vuelve a levantar."""
    config_dir_override: Optional[Path] = ctx.obj.get("config_dir") if ctx.obj else None
    remote_url: Optional[str] = ctx.obj.get("remote_url") if ctx.obj else None
    remote_key: Optional[str] = ctx.obj.get("remote_key") if ctx.obj else None
    config_dir, _ = _resolve_dirs(config_dir_override)

    client, _ = _build_daemon_client(config_dir, remote_url, remote_key)
    _require_daemon(client)

    _handle_daemon_errors(lambda: client.daemon_reload())
    print("Daemon reiniciando...")


@app.command()
def consolidate(
    ctx: typer.Context,
    agent: Optional[str] = typer.Option(
        None,
        "--agent",
        metavar="AGENT_ID",
        help="Consolida solo el agente indicado (ignora memory.enabled). Sin flag → itera todos.",
    ),
) -> None:
    """Consolida la memoria y sale."""
    config_dir_override: Optional[Path] = ctx.obj.get("config_dir") if ctx.obj else None
    remote_url: Optional[str] = ctx.obj.get("remote_url") if ctx.obj else None
    remote_key: Optional[str] = ctx.obj.get("remote_key") if ctx.obj else None
    config_dir, _ = _resolve_dirs(config_dir_override)

    client, _ = _build_daemon_client(config_dir, remote_url, remote_key)
    _require_daemon(client)
    import json

    result = _handle_daemon_errors(lambda: client.consolidate(agent))
    print(json.dumps(result, indent=2, ensure_ascii=False))
