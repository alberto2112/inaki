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
  inaki setup                              → wizard de configuración del sistema
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Optional

import typer

from adapters.inbound.cli.scheduler_cli import scheduler_app

app = typer.Typer(
    name="inaki",
    help="Iñaki — asistente personal agentico",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(scheduler_app, name="scheduler", help="Manage scheduled tasks")


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


def _run_cli(global_config, registry, agent_id: str) -> None:
    """Chat interactivo via CLI."""
    from adapters.inbound.cli.cli_runner import run

    run(global_config, registry, agent_id)


def _run_consolidate(global_config, registry, agent_id: str | None) -> None:
    """
    Ejecuta consolidación de memoria one-shot y sale.

    Sin agent_id → itera todos los agentes habilitados con delay.
    Con agent_id → consolida solo ese agente (ignora memory.enabled).
    """
    from infrastructure.container import AppContainer

    app_container = AppContainer(global_config, registry)

    async def _run() -> None:
        if agent_id:
            try:
                container = app_container.get_agent(agent_id)
            except Exception as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)
            try:
                result = await container.consolidate_memory.execute()
                print(f"{agent_id}: {result}")
            except Exception as exc:
                print(f"Error consolidando '{agent_id}': {exc}", file=sys.stderr)
                sys.exit(1)
        else:
            result = await app_container.consolidate_all_agents.execute()
            print(result)

    asyncio.run(_run())


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


def _build_daemon_client(config_dir: Path):
    """Construye DaemonClient con bootstrap mínimo — solo parsea YAML, sin AppContainer."""
    from infrastructure.config import load_global_config
    from adapters.outbound.daemon_client import DaemonClient

    global_config, _ = load_global_config(config_dir)
    admin = global_config.admin
    client = DaemonClient(
        admin_base_url=f"http://{admin.host}:{admin.port}",
        auth_key=admin.auth_key,
    )
    return client, global_config


def _require_daemon(client) -> None:
    """Verifica que el daemon esté corriendo. Sale con error si no."""
    if not client.health():
        print(
            "El daemon no está corriendo. Iniciá con `inaki daemon` o `systemctl start inaki`.",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)


def _invoke_default_chat(config_dir_override: Optional[Path], standalone: bool = False) -> None:
    """Lanza el chat interactivo con el agente por defecto."""
    config_dir, agents_dir = _resolve_dirs(config_dir_override)
    if standalone:
        global_config, registry = _bootstrap(config_dir, agents_dir)
        agent_id = global_config.app.default_agent
        _run_cli(global_config, registry, agent_id)
    else:
        client, global_config = _build_daemon_client(config_dir)
        _require_daemon(client)
        # En modo daemon-client, delega al bootstrap completo ya que
        # el chat interactivo CLI necesita el event loop local.
        # TODO(phase-2): implementar chat via REST al daemon
        _, registry = _bootstrap(config_dir, agents_dir)
        agent_id = global_config.app.default_agent
        _run_cli(global_config, registry, agent_id)


@app.callback()
def _root(
    ctx: typer.Context,
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        metavar="DIR",
        help="Directorio de configuración (default: ~/.inaki/config)",
    ),
    standalone: bool = typer.Option(
        False,
        "--standalone",
        help="Fuerza bootstrap completo sin requerir daemon (modo legacy)",
    ),
) -> None:
    """Iñaki — asistente personal agentico."""
    ctx.ensure_object(dict)
    ctx.obj["config_dir"] = config
    ctx.obj["standalone"] = standalone
    if ctx.invoked_subcommand is None:
        # bare `inaki` → default chat
        _invoke_default_chat(config, standalone=standalone)


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
    """Chat interactivo con un agente."""
    config_dir_override: Optional[Path] = ctx.obj.get("config_dir") if ctx.obj else None
    standalone: bool = ctx.obj.get("standalone", False) if ctx.obj else False
    config_dir, agents_dir = _resolve_dirs(config_dir_override)

    if standalone:
        global_config, registry = _bootstrap(config_dir, agents_dir)
        agent_id = agent or global_config.app.default_agent
        _run_cli(global_config, registry, agent_id)
    else:
        client, global_config = _build_daemon_client(config_dir)
        _require_daemon(client)
        # Chat interactivo CLI requiere bootstrap completo local
        # TODO(phase-2): implementar chat via REST al daemon
        _, registry = _bootstrap(config_dir, agents_dir)
        agent_id = agent or global_config.app.default_agent
        _run_cli(global_config, registry, agent_id)


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
    standalone: bool = ctx.obj.get("standalone", False) if ctx.obj else False
    config_dir, agents_dir = _resolve_dirs(config_dir_override)

    if standalone:
        global_config, registry = _bootstrap(config_dir, agents_dir)
        agent_id = agent or global_config.app.default_agent
        from adapters.inbound.cli.cli_runner import run_inspect

        run_inspect(global_config, registry, agent_id, message)
    else:
        client, global_config = _build_daemon_client(config_dir)
        _require_daemon(client)
        agent_id = agent or global_config.app.default_agent
        import json

        result = client.inspect(agent_id, message)
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
    standalone: bool = ctx.obj.get("standalone", False) if ctx.obj else False
    config_dir, agents_dir = _resolve_dirs(config_dir_override)

    if standalone:
        global_config, registry = _bootstrap(config_dir, agents_dir)
        _run_consolidate(global_config, registry, agent)
    else:
        client, _ = _build_daemon_client(config_dir)
        _require_daemon(client)
        import json

        result = client.consolidate(agent)
        print(json.dumps(result, indent=2, ensure_ascii=False))


@app.command()
def setup() -> None:
    """Wizard de configuración del sistema (INAKI_SECRET_KEY y otras variables)."""
    from adapters.inbound.cli.setup_wizard import run_setup

    run_setup()
