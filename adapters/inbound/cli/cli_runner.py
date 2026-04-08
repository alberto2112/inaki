"""CLI adapter para Iñaki."""

from __future__ import annotations

import asyncio
import logging

from infrastructure.container import AppContainer
from infrastructure.config import GlobalConfig, AgentRegistry

logger = logging.getLogger(__name__)

_HELP = """Comandos especiales:
  /consolidate       — Extraer recuerdos del historial y archivarlo
  /history           — Ver el historial actual
  /clear             — Limpiar historial sin archivar
  /agents            — Listar agentes disponibles
  /inspect <mensaje> — Ver qué ve el LLM: memorias, skills y tools seleccionadas
  /exit | /quit      — Salir
"""


async def run_cli(app: AppContainer, agent_id: str) -> None:
    """Chat interactivo con un agente."""
    try:
        container = app.get_agent(agent_id)
    except Exception as exc:
        print(f"Error: {exc}")
        return

    agent_cfg = app.registry.get(agent_id)
    print(f"\n🤖 {agent_cfg.name} — {agent_cfg.description}")
    print(f"   Modelo: {agent_cfg.llm.model} via {agent_cfg.llm.provider}")
    print("   Escribe /help para ver comandos. Ctrl+C para salir.\n")

    while True:
        try:
            user_input = input("tú > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nHasta luego.")
            break

        if not user_input:
            continue

        # Comandos especiales
        if user_input in ("/exit", "/quit"):
            print("Hasta luego.")
            break

        if user_input == "/help":
            print(_HELP)
            continue

        if user_input == "/consolidate":
            print("Consolidando memoria...", flush=True)
            try:
                result = await container.consolidate_memory.execute()
                print(f"✓ {result}")
            except Exception as exc:
                print(f"Error: {exc}")
            continue

        if user_input == "/history":
            history = await container.run_agent._history.load(agent_id)
            if not history:
                print("(historial vacío)")
            else:
                for msg in history:
                    print(f"{msg.role.value}: {msg.content}")
            print()
            continue

        if user_input == "/clear":
            await container.run_agent._history.clear(agent_id)
            print("Historial limpiado.")
            continue

        if user_input == "/agents":
            list_agents(app)
            continue

        if user_input.startswith("/inspect"):
            query = user_input[len("/inspect"):].strip()
            if not query:
                print("Uso: /inspect <mensaje>")
            else:
                try:
                    result = await container.run_agent.inspect(query)
                    print_inspect(result)
                except Exception as exc:
                    print(f"Error: {exc}")
            continue

        # Chat normal
        try:
            response = await container.run_agent.execute(user_input)
            print(f"\niñaki > {response}\n")
        except Exception as exc:
            logger.exception("Error procesando mensaje")
            print(f"Error: {exc}\n")


def print_inspect(result) -> None:
    """Imprime el resultado de un inspect de forma legible."""
    W = 60
    print(f"\n{'━' * W}")
    print(f"  RAG Inspect: \"{result.user_input}\"")
    print(f"{'━' * W}\n")

    print(f"📍 Memorias recuperadas ({len(result.memories)}):")
    if result.memories:
        for m in result.memories:
            print(f"   - {m.content}")
    else:
        print("   (ninguna)")

    skills_rag_label = (
        f"RAG activo — {len(result.selected_skills)}/{len(result.all_skills)} seleccionadas"
        if result.skills_rag_active
        else f"RAG inactivo — enviando todas ({len(result.all_skills)})"
    )
    print(f"\n🧠 Skills [{skills_rag_label}]:")
    if result.selected_skills:
        for s in result.selected_skills:
            print(f"   - {s.name}: {s.description}")
    else:
        print("   (ninguna)")

    rag_label = (
        f"RAG activo — {len(result.selected_tool_schemas)}/{len(result.all_tool_schemas)} seleccionadas"
        if result.tools_rag_active
        else f"RAG inactivo — enviando todas ({len(result.all_tool_schemas)})"
    )
    print(f"\n🔧 Tools enviadas al LLM [{rag_label}]:")
    if result.selected_tool_schemas:
        for schema in result.selected_tool_schemas:
            fn = schema.get("function", {})
            print(f"   - {fn.get('name', '?')}: {fn.get('description', '')}")
    else:
        print("   (ninguna)")

    print(f"\n📋 System Prompt final:")
    print(f"   {'─' * (W - 3)}")
    for line in result.system_prompt.splitlines():
        print(f"   {line}")
    print(f"   {'─' * (W - 3)}\n")


def list_agents(app: AppContainer) -> None:
    """Imprime tabla de agentes disponibles."""
    agents = app.registry.list_all()
    print(f"\n{'ID':<12} {'Nombre':<16} Descripción")
    print("-" * 60)
    for a in agents:
        print(f"{a.id:<12} {a.name:<16} {a.description}")
    print()


def run(global_config: GlobalConfig, registry: AgentRegistry, agent_id: str) -> None:
    """Entry point síncrono para el CLI."""
    app = AppContainer(global_config, registry)

    if agent_id == "list":
        list_agents(app)
        return

    asyncio.run(run_cli(app, agent_id))


def run_inspect(
    global_config: GlobalConfig,
    registry: AgentRegistry,
    agent_id: str,
    query: str,
) -> None:
    """One-shot inspect desde --inspect flag de main.py."""
    app = AppContainer(global_config, registry)

    async def _run():
        try:
            container = app.get_agent(agent_id)
        except Exception as exc:
            print(f"Error: {exc}")
            return
        result = await container.run_agent.inspect(query)
        print_inspect(result)

    asyncio.run(_run())
