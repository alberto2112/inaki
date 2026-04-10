"""
Entry point de Iñaki.

Modos de uso:
  python main.py                             → CLI interactivo (agente por defecto)
  python main.py --agent dev                 → CLI con agente específico
  python main.py --agent list                → listar agentes disponibles
  python main.py --inspect "mensaje"         → inspeccionar pipeline RAG sin llamar al LLM
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
    return Path(__file__).parent / "config"


def _bootstrap(config_dir: Path):
    """Carga config, logging y registry. Retorna (global_config, registry)."""
    from infrastructure.config import load_global_config, AgentRegistry
    from infrastructure.logging_setup import setup_logging

    try:
        global_config, global_raw = load_global_config(config_dir)
    except Exception as exc:
        print(f"Error cargando configuración desde {config_dir}: {exc}", file=sys.stderr)
        sys.exit(1)

    setup_logging(global_config.app.log_level)

    registry = AgentRegistry(config_dir, global_raw)
    if not registry.list_all():
        print(
            f"No hay agentes configurados en {config_dir}/agents/. "
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
        "--setup",
        action="store_true",
        help="Wizard de configuración del sistema (INAKI_SECRET_KEY y otras variables)",
    )
    args = parser.parse_args()

    if args.setup:
        from adapters.inbound.cli.setup_wizard import run_setup
        run_setup()
        return

    config_dir = Path(args.config) if args.config else _get_config_dir()
    global_config, registry = _bootstrap(config_dir)

    if args.daemon:
        if args.agent:
            print("--agent no tiene efecto en modo --daemon (se levantan todos los agentes)", file=sys.stderr)
        _run_daemon(global_config, registry)
    elif args.inspect is not None:
        agent_id = args.agent or global_config.app.default_agent
        from adapters.inbound.cli.cli_runner import run_inspect
        run_inspect(global_config, registry, agent_id, args.inspect)
    else:
        agent_id = args.agent or global_config.app.default_agent
        _run_cli(global_config, registry, agent_id)


if __name__ == "__main__":
    main()
