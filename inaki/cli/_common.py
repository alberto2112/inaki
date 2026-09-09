"""Helpers compartidos por los comandos del CLI: home, cliente del daemon, errores.

Los comandos los invocan como ``_common.<fn>`` (atributo del módulo, no import
directo) para que un test pueda parchear ``inaki.cli._common.build_daemon_client``
y todos los comandos lo vean.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer

from inaki.config.home import get_inaki_home


def get_config_dir() -> Path:
    return get_inaki_home() / "config"


def get_agents_dir() -> Path:
    return get_inaki_home() / "agents"


def resolve_dirs():
    """Resuelve config_dir y agents_dir del home de instancia, bootstrapeando si hace falta.

    El home se fija en el callback raíz (``--home`` / ``INAKI_HOME``) vía ``set_inaki_home``;
    acá todo deriva de ``get_inaki_home()``. No hay override de config_dir suelto: el único
    knob de relocalización es el home (ver docs/instance-home.md)."""
    from inaki.config import ensure_user_config
    from inaki.config.boundary import borde_de_config

    config_dir = get_config_dir()
    agents_dir = get_agents_dir()
    # Las migraciones releen y reescriben los YAML con ruamel: un fichero que ni
    # parsea (indentación, clave duplicada) revienta ACÁ, antes de que ningún
    # loader llegue a validar nada.
    with borde_de_config(str(get_inaki_home())):
        ensure_user_config(config_dir, agents_dir)
    return config_dir, agents_dir


def build_daemon_client(
    config_dir: Path,
    remote_url: Optional[str] = None,
    remote_key: Optional[str] = None,
):
    """Construye DaemonClient con bootstrap mínimo — solo parsea YAML, sin AppContainer.

    Si `remote_url` está definido, apunta al daemon remoto en vez del local.
    El auth key se resuelve: `remote_key` > `admin.auth_key` del config local.
    """
    from inaki.cli.client import DaemonClient
    from inaki.config import load_global_config
    from inaki.config.boundary import borde_de_config

    # Mismo borde que `_bootstrap`: este es el camino de `inaki` / `inaki chat`,
    # o sea el primer comando que tipea el operador cuando algo no anda.
    with borde_de_config(str(config_dir)):
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


def handle_daemon_errors(fn):
    """Ejecuta `fn` y mapea errores del daemon a mensajes limpios + typer.Exit(1)."""
    from inaki.shared.errors import (
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


def require_daemon(client) -> None:
    """Verifica que el daemon esté corriendo. Sale con error si no."""
    if not client.health():
        print(
            "El daemon no está corriendo. Iniciá con `inaki daemon` o `systemctl start inaki`.",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)
