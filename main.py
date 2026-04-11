"""
Entry point de Iñaki.

Modos de uso:
  python main.py                             → CLI interactivo (agente por defecto)
  python main.py --agent dev                 → CLI con agente específico
  python main.py --agent list                → listar agentes disponibles
  python main.py --inspect "mensaje"         → inspeccionar pipeline RAG sin llamar al LLM
  python main.py --consolidate               → consolida TODOS los agentes habilitados (con delay)
  python main.py --consolidate --agent dev   → consolida solo el agente indicado
  python main.py --daemon                    → servicio systemd (todos los canales de todos los agentes)
  python main.py --daemon --config /etc/inaki/config  → daemon con config custom
  python main.py --setup                     → wizard de configuración del sistema
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


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


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="inaki",
        description="Iñaki — asistente personal agentico",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  inaki                                Chat CLI con el agente por defecto
  inaki --agent dev                    Chat CLI con el agente 'dev'
  inaki --agent list                   Listar agentes disponibles
  inaki --inspect "busca el dolar"     Inspeccionar RAG sin llamar al LLM
  inaki --daemon                       Modo servicio (Telegram + REST para todos los agentes)
  inaki --daemon --config /etc/inaki/config
        """,
    )
    parser.add_argument(
        "--agent",
        default=None,
        metavar="AGENT_ID|list",
        help="ID del agente o 'list' para listar agentes (solo en modo CLI)",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Arrancar como servicio systemd (levanta todos los canales)",
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="DIR",
        help="Directorio de configuración (default: ./config)",
    )
    parser.add_argument(
        "--inspect",
        default=None,
        metavar="MENSAJE",
        help="Inspeccionar el pipeline RAG para un mensaje sin llamar al LLM",
    )
    parser.add_argument(
        "--consolidate",
        action="store_true",
        help="Consolida la memoria y sale. Sin --agent itera todos los agentes habilitados con delay.",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Wizard de configuración del sistema (INAKI_SECRET_KEY y otras variables)",
    )
    args = parser.parse_args()

    if args.setup:
        from adapters.inbound.cli.setup_wizard import run_setup
        run_setup()
        return

    if args.config:
        config_dir = Path(args.config)
        agents_dir = config_dir / "agents"
    else:
        from infrastructure.config import ensure_user_config
        config_dir = _get_config_dir()
        agents_dir = _get_agents_dir()
        ensure_user_config(config_dir, agents_dir)

    global_config, registry = _bootstrap(config_dir, agents_dir)

    if args.daemon:
        if args.agent:
            print("--agent no tiene efecto en modo --daemon (se levantan todos los agentes)", file=sys.stderr)
        _run_daemon(global_config, registry)
    elif args.consolidate:
        _run_consolidate(global_config, registry, args.agent)
    elif args.inspect is not None:
        agent_id = args.agent or global_config.app.default_agent
        from adapters.inbound.cli.cli_runner import run_inspect
        run_inspect(global_config, registry, agent_id, args.inspect)
    else:
        agent_id = args.agent or global_config.app.default_agent
        _run_cli(global_config, registry, agent_id)


def _run_consolidate(global_config, registry, agent_id: str | None) -> None:
    """
    Ejecuta consolidación de memoria one-shot y sale.

    Sin agent_id → itera todos los agentes habilitados con delay.
    Con agent_id → consolida solo ese agente (ignora memory.enabled).
    """
    from infrastructure.container import AppContainer

    app = AppContainer(global_config, registry)

    async def _run() -> None:
        if agent_id:
            try:
                container = app.get_agent(agent_id)
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
            result = await app.consolidate_all_agents.execute()
            print(result)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
