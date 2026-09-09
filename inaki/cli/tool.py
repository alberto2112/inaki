"""Comando ``tool``: invoca una tool del agente sin LLM, o lista las disponibles."""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

import typer

from inaki.cli import _common


def parsear_arg(par: str) -> tuple[str, Any]:
    """Parsea un arg en formato 'k=v' y convierte el valor a tipo Python.

    Intentos de conversión (en orden): JSON literal → string crudo.
    Si no hay '=' en el string → error de validación (exit 2).
    """
    if "=" not in par:
        print(
            f"Argumento malformado '{par}': el formato esperado es K=V (ej. --arg n=5).",
            file=sys.stderr,
        )
        raise typer.Exit(code=2)

    clave, valor = par.split("=", 1)

    if not valor:
        return clave, ""

    # Intentar parsear como JSON literal (int, float, bool, null, listas, dicts, strings)
    try:
        return clave, json.loads(valor)
    except (json.JSONDecodeError, ValueError):
        return clave, valor


def tool(
    ctx: typer.Context,
    tool_name: Optional[str] = typer.Argument(
        None,
        metavar="NAME",
        help="Nombre de la tool a invocar. Mutex con --list.",
    ),
    list_: bool = typer.Option(
        False,
        "--list",
        "-l",
        help="Lista las tools disponibles del agente.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Con --list: muestra descripción y esquema de parámetros.",
    ),
    agent: Optional[str] = typer.Option(
        None,
        "--agent",
        metavar="AGENT_ID",
        help="ID del agente (default: agente por defecto del global).",
    ),
    arg: list[str] = typer.Option(
        [],
        "--arg",
        "-a",
        metavar="K=V",
        help="Argumento para la invocación de la tool (repetible). Mutex con --json.",
    ),
    json_args: Optional[str] = typer.Option(
        None,
        "--json",
        metavar="JSON",
        help="Argumentos como JSON object. Mutex con --arg.",
    ),
    raw: bool = typer.Option(
        False,
        "--raw",
        help="Imprime el output crudo sin formatear como JSON.",
    ),
) -> None:
    """Invoca una tool del agente directamente (sin LLM) o lista las disponibles."""
    # --- Validaciones de uso ------------------------------------------------
    if list_ and tool_name:
        print(
            "Error: usá --list O un nombre de tool, no ambos a la vez.",
            file=sys.stderr,
        )
        raise typer.Exit(code=2)

    if not list_ and not tool_name:
        print(
            "Error: especificá --list para listar o un nombre de tool para invocar.",
            file=sys.stderr,
        )
        raise typer.Exit(code=2)

    if arg and json_args is not None:
        print(
            "Error: --arg y --json son mutuamente excluyentes.",
            file=sys.stderr,
        )
        raise typer.Exit(code=2)

    # --- Setup común --------------------------------------------------------
    remote_url: Optional[str] = ctx.obj.get("remote_url") if ctx.obj else None
    remote_key: Optional[str] = ctx.obj.get("remote_key") if ctx.obj else None
    config_dir, _ = _common.resolve_dirs()

    client, global_config = _common.build_daemon_client(config_dir, remote_url, remote_key)
    _common.require_daemon(client)
    agent_id = agent or global_config.app.default_agent

    # --- Listar tools -------------------------------------------------------
    if list_:
        resultado = _common.handle_daemon_errors(lambda: client.list_tools(agent_id))
        tools: list[dict[str, Any]] = resultado.get("tools", [])
        if not verbose:
            for t in tools:
                print(t["name"])
        else:
            from rich.console import Console
            from rich.table import Table

            consola = Console()
            tabla = Table(show_header=True, header_style="bold")
            tabla.add_column("Nombre", style="cyan")
            tabla.add_column("Descripción")
            tabla.add_column("Parámetros (schema)")
            for t in tools:
                schema_str = json.dumps(
                    t.get("parameters_schema", {}), indent=2, ensure_ascii=False
                )
                tabla.add_row(t["name"], t.get("description", ""), schema_str)
            consola.print(tabla)
        return

    # --- Invocar tool -------------------------------------------------------
    # Construir args dict
    args_dict: dict[str, Any] = {}
    if json_args is not None:
        try:
            args_dict = json.loads(json_args)
        except json.JSONDecodeError as exc:
            print(f"Error: --json no es JSON válido: {exc}", file=sys.stderr)
            raise typer.Exit(code=2)
        if not isinstance(args_dict, dict):
            print("Error: --json debe ser un JSON object ({...}).", file=sys.stderr)
            raise typer.Exit(code=2)
    elif arg:
        for par in arg:
            clave, valor = parsear_arg(par)
            args_dict[clave] = valor

    resultado = _common.handle_daemon_errors(
        lambda: client.invoke_tool(agent_id, tool_name, args_dict)  # type: ignore[arg-type]
    )

    if not resultado.get("success", False):
        print(
            f"Error en tool '{resultado.get('tool_name', tool_name)}': "
            f"{resultado.get('error', 'error desconocido')}",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)

    output: str = resultado.get("output", "")
    if raw:
        print(output)
    else:
        try:
            datos = json.loads(output)
            print(json.dumps(datos, indent=2, ensure_ascii=False))
        except (json.JSONDecodeError, TypeError):
            print(output)
