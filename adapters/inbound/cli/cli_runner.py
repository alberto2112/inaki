"""CLI adapter para Iñaki — REPL sync sobre IDaemonClient.

El chat interactivo delega al daemon vía HTTP. No instancia AppContainer.
Solo usa IDaemonClient (port) para todas las operaciones de conversación.
"""

from __future__ import annotations

import json
import logging
import uuid

from rich.console import Console

from core.domain.errors import (
    DaemonClientError,
    DaemonNotRunningError,
    DaemonTimeoutError,
)
from core.ports.outbound.daemon_client_port import IDaemonClient

logger = logging.getLogger(__name__)
console = Console()

_HELP = """Comandos especiales:
  /consolidate       — Extraer recuerdos del historial y archivarlo
  /history           — Ver el historial actual
  /clear             — Limpiar historial sin archivar
  /agents            — Listar agentes disponibles
  /inspect <mensaje> — Ver qué ve el LLM: memorias, skills y tools seleccionadas
  /exit | /quit      — Salir
"""


def run_cli(client: IDaemonClient, agent_id: str) -> None:
    """Chat interactivo sync con un agente via daemon HTTP.

    Genera un session_id UUID en memoria al inicio del proceso.
    Todas las llamadas al daemon usan ese session_id.
    """
    session_id = str(uuid.uuid4())

    print(f"\niñaki > Conectado al agente '{agent_id}'. Escribe /help para ver comandos. Ctrl+C para salir.\n")

    while True:
        try:
            user_input = input("tú > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nHasta luego.")
            return

        if not user_input:
            continue

        # Comandos especiales
        if user_input in ("/exit", "/quit"):
            print("Hasta luego.")
            return

        if user_input == "/help":
            print(_HELP)
            continue

        if user_input == "/clear":
            try:
                client.chat_clear(agent_id)
                print("Historial limpiado.")
            except DaemonNotRunningError as exc:
                print(f"\n{exc}\nSaliendo.")
                return
            except DaemonClientError as exc:
                print(f"\nError: {exc}")
            continue

        if user_input == "/history":
            try:
                msgs = client.chat_history(agent_id)
            except DaemonNotRunningError as exc:
                print(f"\n{exc}\nSaliendo.")
                return
            except DaemonClientError as exc:
                print(f"\nError: {exc}")
                continue
            if not msgs:
                print("(historial vacío)")
            else:
                for msg in msgs:
                    print(f"{msg['role']}: {msg['content']}")
            print()
            continue

        if user_input == "/consolidate":
            try:
                result = client.consolidate(agent_id)
                print(f"✓ {result}")
            except DaemonNotRunningError as exc:
                print(f"\n{exc}\nSaliendo.")
                return
            except DaemonClientError as exc:
                print(f"\nError: {exc}")
            continue

        if user_input == "/agents":
            try:
                agentes = client.list_agents()
                if agentes:
                    print("\nAgentes disponibles:")
                    for a in agentes:
                        print(f"  - {a}")
                    print()
                else:
                    print("(no hay agentes registrados)")
            except DaemonNotRunningError as exc:
                print(f"\n{exc}")
                continue
            except DaemonClientError as exc:
                print(f"\nError al listar agentes: {exc}")
            continue

        if user_input.startswith("/inspect"):
            query = user_input[len("/inspect"):].strip()
            if not query:
                print("Uso: /inspect <mensaje>")
            else:
                try:
                    result = client.inspect(agent_id, query)
                    print_inspect(result)
                except DaemonNotRunningError as exc:
                    print(f"\n{exc}\nSaliendo.")
                    return
                except DaemonClientError as exc:
                    print(f"\nError: {exc}")
            continue

        # --- Turno de chat normal ---
        try:
            with console.status("Pensando...", spinner="dots"):
                turn_result = client.chat_turn(agent_id, session_id, user_input)
            # Mensajes intermedios (narración emitida junto con tool_calls):
            # los mostramos antes de la respuesta final para que el usuario
            # vea la progresión del turno tal cual la hizo el agente.
            for intermediate in turn_result.intermediates:
                print(f"\niñaki > {intermediate}")
            print(f"\niñaki > {turn_result.reply}\n")
        except KeyboardInterrupt:
            print("\nHasta luego.")
            return
        except DaemonNotRunningError as exc:
            print(f"\n{exc}\nSaliendo.")
            return
        except DaemonTimeoutError as exc:
            print(f"\nTimeout esperando respuesta del daemon. Intentá de nuevo.")
            logger.warning("Timeout en chat_turn: %s", exc)
            continue
        except DaemonClientError as exc:
            print(f"\nError: {exc}")
            continue


def print_inspect(result) -> None:
    """Imprime el resultado de un inspect de forma legible."""
    W = 60
    print(f"\n{'━' * W}")
    print(f"  RAG Inspect: \"{result.get('user_input', '?')}\"")
    print(f"{'━' * W}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


def list_agents_from_registry(registry) -> None:
    """Imprime tabla de agentes desde un registry liviano."""
    agents = registry.list_all()
    print(f"\n{'ID':<12} {'Nombre':<16} Descripción")
    print("-" * 60)
    for a in agents:
        print(f"{a.id:<12} {a.name:<16} {a.description}")
    print()


def run(global_config, registry, client: IDaemonClient, agent_id: str) -> None:
    """Entry point síncrono para el CLI — nueva firma sin AppContainer."""
    if agent_id == "list":
        list_agents_from_registry(registry)
        return

    run_cli(client, agent_id)


def run_inspect(
    global_config,
    registry,
    client: IDaemonClient,
    agent_id: str,
    query: str,
) -> None:
    """One-shot inspect desde --inspect flag de main.py."""
    result = client.inspect(agent_id, query)
    print(json.dumps(result, indent=2, ensure_ascii=False))
