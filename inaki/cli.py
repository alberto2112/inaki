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
  inaki --config /etc/inaki/config daemon  → daemon con config custom
  inaki --remote http://raspi.local:6497   → conectarse a un daemon remoto (env: INAKI_REMOTE)
  inaki --remote URL --remote-key KEY chat → conectarse a daemon remoto con auth key explícita
  inaki setup                              → wizard de configuración del sistema
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Optional

import typer

from adapters.inbound.cli.knowledge_cli import knowledge_app
from adapters.inbound.cli.scheduler_cli import scheduler_app

app = typer.Typer(
    name="inaki",
    help="Iñaki — asistente personal agentico",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(scheduler_app, name="scheduler", help="Manage scheduled tasks")
app.add_typer(knowledge_app, name="knowledge", help="Manage document knowledge sources")


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


def _run_daemon(global_config, registry) -> None:
    """Arranca todos los canales en modo servicio systemd."""
    import logging
    from infrastructure.container import AppContainer
    from adapters.inbound.daemon.runner import run_daemon

    logger = logging.getLogger(__name__)
    logger.info("Iniciando Iñaki en modo daemon")

    app_container = AppContainer(global_config, registry)
    asyncio.run(run_daemon(app_container, registry))


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
) -> None:
    """Iñaki — asistente personal agentico."""
    ctx.ensure_object(dict)
    ctx.obj["config_dir"] = config
    ctx.obj["remote_url"] = remote
    ctx.obj["remote_key"] = remote_key
    if ctx.invoked_subcommand is None:
        # bare `inaki` → default chat
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
) -> None:
    """Chat interactivo con un agente via daemon HTTP."""
    config_dir_override: Optional[Path] = ctx.obj.get("config_dir") if ctx.obj else None
    remote_url: Optional[str] = ctx.obj.get("remote_url") if ctx.obj else None
    remote_key: Optional[str] = ctx.obj.get("remote_key") if ctx.obj else None
    config_dir, _ = _resolve_dirs(config_dir_override)

    client, global_config = _build_daemon_client(config_dir, remote_url, remote_key)
    _require_daemon(client)
    agent_id = agent or global_config.app.default_agent
    _run_cli(client, agent_id)


@app.command()
def daemon(
    ctx: typer.Context,
) -> None:
    """Arranca como servicio systemd (levanta todos los canales de todos los agentes)."""
    config_dir_override: Optional[Path] = ctx.obj.get("config_dir") if ctx.obj else None
    config_dir, agents_dir = _resolve_dirs(config_dir_override)
    global_config, registry = _bootstrap(config_dir, agents_dir)
    _run_daemon(global_config, registry)


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


@app.command()
def setup() -> None:
    """Wizard de configuración del sistema (INAKI_SECRET_KEY y otras variables)."""
    from adapters.inbound.cli.setup_wizard import run_setup

    run_setup()
