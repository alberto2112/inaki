"""Comandos de operación contra el daemon: ``inspect``, ``reload``, ``consolidate`` y ``gen-docs``."""

from __future__ import annotations

import json
from typing import Optional

import typer

from inaki.cli import _common


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
    remote_url: Optional[str] = ctx.obj.get("remote_url") if ctx.obj else None
    remote_key: Optional[str] = ctx.obj.get("remote_key") if ctx.obj else None
    config_dir, _ = _common.resolve_dirs()

    client, global_config = _common.build_daemon_client(config_dir, remote_url, remote_key)
    _common.require_daemon(client)
    agent_id = agent or global_config.app.default_agent
    import json

    result = _common.handle_daemon_errors(lambda: client.inspect(agent_id, message))
    print(json.dumps(result, indent=2, ensure_ascii=False))


def reload(
    ctx: typer.Context,
) -> None:
    """Reinicia el daemon: cierra todos los channels, recarga config y vuelve a levantar."""
    remote_url: Optional[str] = ctx.obj.get("remote_url") if ctx.obj else None
    remote_key: Optional[str] = ctx.obj.get("remote_key") if ctx.obj else None
    config_dir, _ = _common.resolve_dirs()

    client, _ = _common.build_daemon_client(config_dir, remote_url, remote_key)
    _common.require_daemon(client)

    _common.handle_daemon_errors(lambda: client.daemon_reload())
    print("Daemon reiniciando...")


def gen_docs() -> None:
    """[dev] Regenera la documentación derivada del schema de configuración.

    Dos artefactos, una sola fuente (los docstrings del schema):
      - `docs/config-reference.md`   — referencia exhaustiva de campos.
      - `config/global.example.yaml` — referencia comentada en formato YAML.

    Tooling de desarrollo: solo tiene sentido desde el repo. El test de drift
    `test_config_docs_drift.py` falla si alguno quedó desincronizado del
    schema — correr esto lo resuelve.
    """
    from pathlib import Path

    from inaki.config.docs import generate_config_reference, generate_global_example

    raiz = Path(__file__).resolve().parents[2]
    artefactos = (
        (raiz / "docs" / "config-reference.md", generate_config_reference),
        (raiz / "config" / "global.example.yaml", generate_global_example),
    )
    for destino, generar in artefactos:
        if not destino.parent.is_dir():
            typer.echo(f"error: no existe {destino.parent} (¿fuera del repo?)", err=True)
            raise typer.Exit(1)
        destino.write_text(generar(), encoding="utf-8")
        typer.echo(f"✓ regenerado {destino}")


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
    remote_url: Optional[str] = ctx.obj.get("remote_url") if ctx.obj else None
    remote_key: Optional[str] = ctx.obj.get("remote_key") if ctx.obj else None
    config_dir, _ = _common.resolve_dirs()

    client, _ = _common.build_daemon_client(config_dir, remote_url, remote_key)
    _common.require_daemon(client)
    result = _common.handle_daemon_errors(lambda: client.consolidate(agent))
    print(json.dumps(result, indent=2, ensure_ascii=False))
