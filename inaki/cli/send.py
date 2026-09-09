"""Comando ``send``: envía texto o media a un canal externo sin pasar por el LLM."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import typer

from inaki.cli import _common


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
    remote_url: Optional[str] = ctx.obj.get("remote_url") if ctx.obj else None
    remote_key: Optional[str] = ctx.obj.get("remote_key") if ctx.obj else None
    config_dir, _ = _common.resolve_dirs()

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
    client, global_config = _common.build_daemon_client(config_dir, remote_url, remote_key)
    _common.require_daemon(client)
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

    _common.handle_daemon_errors(
        lambda: client.send_message_via(agent_id, canal, chat_id, kind, **kwargs)
    )
    print(f"✓ enviado a {canal}:{chat_id} ({kind})")
