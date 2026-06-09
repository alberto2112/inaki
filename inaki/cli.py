"""
Entry point de Inaki.

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
  inaki tool <name> [--arg k=v ...]        → ejecuta una tool del agente sin LLM
  inaki tool --list                        → lista tools del agente
  inaki send <channel>:<chat_id> --text ...→ envía mensaje/archivo via canal (sin LLM, persiste)
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Optional

import typer

from adapters.inbound.cli.knowledge_cli import knowledge_app
from adapters.inbound.cli.scheduler_cli import scheduler_app
from adapters.inbound.cli.setup_cli import setup_app
from inaki import __version__

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
    logger.info("Iniciando Inaki en modo daemon")

    initial_container = AppContainer(global_config, registry)

    # Crea ~/.inaki/users/{channel}/ por cada canal configurado en cualquier agente.
    # Lazy + idempotente: cero costo si ya existen. Habilita la convención per-user
    # (ver docs/configuracion.md → "Per-user context files").
    from infrastructure.config import ensure_user_channel_dirs

    ensure_user_channel_dirs(Path.home(), registry.list_all())

    def bootstrap_fn():
        gc, reg = _bootstrap(config_dir, agents_dir)
        ensure_user_channel_dirs(Path.home(), reg.list_all())
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
    """Inaki — asistente personal agentico."""
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
    result = _handle_daemon_errors(lambda: client.consolidate(agent))
    print(json.dumps(result, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Helpers para el comando `tool`
# ---------------------------------------------------------------------------


def _parsear_arg(par: str) -> tuple[str, Any]:
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


@app.command()
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
    config_dir_override: Optional[Path] = ctx.obj.get("config_dir") if ctx.obj else None
    remote_url: Optional[str] = ctx.obj.get("remote_url") if ctx.obj else None
    remote_key: Optional[str] = ctx.obj.get("remote_key") if ctx.obj else None
    config_dir, _ = _resolve_dirs(config_dir_override)

    client, global_config = _build_daemon_client(config_dir, remote_url, remote_key)
    _require_daemon(client)
    agent_id = agent or global_config.app.default_agent

    # --- Listar tools -------------------------------------------------------
    if list_:
        resultado = _handle_daemon_errors(lambda: client.list_tools(agent_id))
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
            clave, valor = _parsear_arg(par)
            args_dict[clave] = valor

    resultado = _handle_daemon_errors(
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


# ---------------------------------------------------------------------------
# Comando `send`
# ---------------------------------------------------------------------------


@app.command()
def send(
    ctx: typer.Context,
    destination: str = typer.Argument(
        ...,
        metavar="CHANNEL:CHAT_ID",
        help="Destino en formato 'canal:chat_id' (ej. telegram:4879536 o telegram:-1001234567890).",
    ),
    text: Optional[str] = typer.Option(
        None,
        "--text",
        help="Texto a enviar. Mutex con --photo, --audio, --video, --file, --album.",
    ),
    photo: Optional[Path] = typer.Option(
        None,
        "--photo",
        help="Path a imagen. Mutex con el resto de flags de contenido.",
    ),
    audio: Optional[Path] = typer.Option(
        None,
        "--audio",
        help="Path a audio. Mutex con el resto de flags de contenido.",
    ),
    video: Optional[Path] = typer.Option(
        None,
        "--video",
        help="Path a video. Mutex con el resto de flags de contenido.",
    ),
    file_: Optional[Path] = typer.Option(
        None,
        "--file",
        metavar="PATH",
        help="Path a archivo genérico. Mutex con el resto de flags de contenido.",
    ),
    album: list[Path] = typer.Option(
        [],
        "--album",
        help="Path a imagen del álbum (repetible). Mutex con el resto de flags de contenido.",
    ),
    caption: Optional[str] = typer.Option(
        None,
        "--caption",
        help="Descripción adjunta a media. No válido con --text.",
    ),
    agent: Optional[str] = typer.Option(
        None,
        "--agent",
        metavar="AGENT_ID",
        help="ID del agente desde el que se envía (default: agente por defecto del global).",
    ),
    no_broadcast: bool = typer.Option(
        False,
        "--no-broadcast",
        help=(
            "No emitir BroadcastMessage al LAN tras envío (escape hatch para "
            "scripts CI o casos donde no querés que otros bots vean este mensaje). "
            "Solo aplica para envíos de texto a Telegram."
        ),
    ),
) -> None:
    """Envía un mensaje o archivo a un canal externo sin pasar por el LLM."""
    # --- Parsear destination ------------------------------------------------
    if ":" not in destination:
        print(
            f"Error: destino malformado '{destination}'. "
            "Formato esperado: CANAL:CHAT_ID (ej. telegram:4879536).",
            file=sys.stderr,
        )
        raise typer.Exit(code=2)

    canal, chat_id = destination.split(":", 1)
    if not canal or not chat_id:
        print(
            f"Error: destino malformado '{destination}'. "
            "Tanto el canal como el chat_id deben ser no vacíos (ej. telegram:4879536).",
            file=sys.stderr,
        )
        raise typer.Exit(code=2)

    # --- Validar flags de contenido (mutex, exactamente 1) -------------------
    flags_contenido = {
        "text": text,
        "photo": photo,
        "audio": audio,
        "video": video,
        "file": file_,
        "album": album if album else None,
    }
    flags_activos = [nombre for nombre, val in flags_contenido.items() if val is not None]

    if len(flags_activos) == 0:
        print(
            "Error: especificá uno de: --text, --photo, --audio, --video, --file, --album.",
            file=sys.stderr,
        )
        raise typer.Exit(code=2)

    if len(flags_activos) > 1:
        print(
            f"Error: --{flags_activos[0]} y --{flags_activos[1]} son mutuamente excluyentes. "
            "Usá solo uno a la vez.",
            file=sys.stderr,
        )
        raise typer.Exit(code=2)

    tipo_activo = flags_activos[0]

    # --- Validar --caption no combinado con --text ---------------------------
    if caption is not None and tipo_activo == "text":
        print(
            "Error: --caption no es válido junto a --text. "
            "Usá --caption solo con media (--photo, --audio, --video, --file, --album).",
            file=sys.stderr,
        )
        raise typer.Exit(code=2)

    # --- Setup común --------------------------------------------------------
    config_dir_override: Optional[Path] = ctx.obj.get("config_dir") if ctx.obj else None
    remote_url: Optional[str] = ctx.obj.get("remote_url") if ctx.obj else None
    remote_key: Optional[str] = ctx.obj.get("remote_key") if ctx.obj else None
    config_dir, _ = _resolve_dirs(config_dir_override)

    # --- Validar paths locales (solo si no es daemon remoto) -----------------
    es_remoto = bool(remote_url)
    paths_a_validar: list[Path] = []
    if tipo_activo == "photo" and photo:
        paths_a_validar = [photo]
    elif tipo_activo == "audio" and audio:
        paths_a_validar = [audio]
    elif tipo_activo == "video" and video:
        paths_a_validar = [video]
    elif tipo_activo == "file" and file_:
        paths_a_validar = [file_]
    elif tipo_activo == "album":
        paths_a_validar = list(album)

    if paths_a_validar:
        if es_remoto:
            print(
                "Advertencia: operando contra daemon remoto — paths no validados localmente.",
                file=sys.stderr,
            )
        else:
            for ruta in paths_a_validar:
                if not ruta.exists() or not ruta.is_file():
                    print(
                        f"Error: el archivo '{ruta}' no existe o no es un archivo válido.",
                        file=sys.stderr,
                    )
                    raise typer.Exit(code=2)

    # --- Conectar al daemon -------------------------------------------------
    client, global_config = _build_daemon_client(config_dir, remote_url, remote_key)
    _require_daemon(client)
    agent_id = agent or global_config.app.default_agent

    # --- Construir parámetros de envío --------------------------------------
    kind = tipo_activo
    kwargs: dict[str, Any] = {}

    if kind == "text":
        kwargs["text"] = text
    elif kind == "photo":
        kwargs["sources"] = [str(photo)]
        if caption is not None:
            kwargs["caption"] = caption
    elif kind == "audio":
        kwargs["sources"] = [str(audio)]
        if caption is not None:
            kwargs["caption"] = caption
    elif kind == "video":
        kwargs["sources"] = [str(video)]
        if caption is not None:
            kwargs["caption"] = caption
    elif kind == "file":
        kwargs["sources"] = [str(file_)]
        if caption is not None:
            kwargs["caption"] = caption
    elif kind == "album":
        kwargs["sources"] = [str(p) for p in album]
        if caption is not None:
            kwargs["caption"] = caption

    respuesta: dict[str, Any] = _handle_daemon_errors(
        lambda: client.send_message_via(
            agent_id, canal, chat_id, kind, broadcast=not no_broadcast, **kwargs
        )
    )
    sufijo = " [broadcast]" if (respuesta or {}).get("broadcasted") else ""
    print(f"✓ enviado a {canal}:{chat_id} ({kind}){sufijo}")
