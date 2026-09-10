"""Entry point del CLI ``inaki`` — composition root de los comandos.

Cada comando vive en su módulo (``init``, ``chat``, ``daemon``, ``admin``, ``tool``, ``send``,
``scheduler``, ``knowledge``, ``service``) y se registra acá. Los helpers compartidos están en
``_common``; el bootstrap del daemon en ``inaki.app``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer

from inaki import __version__
from inaki.channels import registrar_canales_instalados
from inaki.cli import admin, chat, config_web, daemon, init, send, tool
from inaki.cli.chat import invoke_default_chat, invoke_task
from inaki.cli.knowledge import knowledge_app
from inaki.cli.scheduler import scheduler_app
from inaki.cli.service import service_app
from inaki.config.cli import config_app
from inaki.config.home import set_inaki_home
from inaki.observability import set_debug_override

app = typer.Typer(
    name="inaki",
    help="Inaki — asistente personal agentico",
    invoke_without_command=True,
    no_args_is_help=False,
)


def _version_callback(value: bool) -> None:
    if value:
        print(f"inaki {__version__}")
        raise typer.Exit()


app.add_typer(scheduler_app, name="scheduler", help="Manage scheduled tasks")
app.add_typer(knowledge_app, name="knowledge", help="Manage document knowledge sources")
# `config web` monta un canal: es del composition root, no de inaki/config.
config_app.command("web")(config_web.web)
app.add_typer(config_app, name="config", help="Inspeccionar y editar la configuración efectiva")
app.add_typer(service_app, name="service", help="Instalar o quitar la unidad systemd")

app.command()(init.init)
app.command()(chat.chat)
app.command()(daemon.daemon)
app.command()(admin.inspect)
app.command()(admin.reload)
app.command(name="gen-docs", hidden=True)(admin.gen_docs)
app.command()(admin.consolidate)
app.command()(tool.tool)
app.command()(send.send)


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
    home: Optional[Path] = typer.Option(
        None,
        "--home",
        metavar="DIR",
        envvar="INAKI_HOME",
        help="Raíz del home de la instancia (default: ~/.inaki). Reancla config, data, "
        "secret.key, tool_config y users. Una 2ª instancia aislada usa otro --home/INAKI_HOME.",
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
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Modo diagnóstico para ESTE arranque: nivel DEBUG y trazas de turno en "
        "<home>/debug/turns/. Gana sobre app.debug del YAML.",
    ),
) -> None:
    """Inaki — asistente personal agentico."""
    ctx.ensure_object(dict)
    # Los canales instalados registran su sección de config ANTES de cargar nada.
    registrar_canales_instalados()
    if debug:
        set_debug_override(True)
    if home is not None:
        set_inaki_home(home)
        # Propagar a env: los adapters que NO pueden importar infra (setup TUI,
        # config_repository) leen INAKI_HOME directo; también lo heredan procesos hijos.
        os.environ["INAKI_HOME"] = str(home)
    ctx.obj["remote_url"] = remote
    ctx.obj["remote_key"] = remote_key
    if ctx.invoked_subcommand is None:
        if task:
            invoke_task(remote, remote_key, None, task, channel=channel, chat_id=chat_id)
        else:
            invoke_default_chat(remote, remote_key)
