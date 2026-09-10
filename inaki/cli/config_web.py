"""``inaki config web`` — la UI de config servida standalone.

Vive en el composition root y no en ``inaki/config/cli.py`` porque MONTA un
canal (el router de ``inaki/channels/rest``) sobre un FastAPI propio, y
``inaki/config`` solo conoce el kernel y ``shared``: el contrato de
``import-linter`` lo prohíbe, y con razón — config es un módulo, no un
ensamblador. Se registra sobre ``config_app`` desde ``inaki/cli/__init__.py``.

Dos modos según dónde escucha:

- **Loopback** (default): sin auth. En la máquina del operador, editar por el
  navegador es el mismo nivel de confianza que abrir el YAML con un editor.
- **Cualquier otra interfaz** (``--host 0.0.0.0``, una IP de la LAN): exige
  ``admin.auth_key`` con la MISMA dependencia que el admin server del daemon
  (``X-Admin-Key``). Sin key configurada no arranca: un editor de credenciales
  abierto a la red sin auth no es un default, es un agujero.

Para gestionar una instancia en remoto el camino natural es el daemon
(``admin.host: 0.0.0.0`` + ``admin.auth_key`` → ``/admin/config/ui``), que además
puede recargarse tras guardar. Este comando cubre el caso sin daemon.
"""

from __future__ import annotations

from typing import Any

import typer

from inaki.cli import _common

HOSTS_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def es_loopback(host: str) -> bool:
    return host in HOSTS_LOOPBACK


def construir_app(*, host: str, auth_key: str | None) -> Any:
    """El FastAPI standalone. Fuera de loopback, ``auth_key`` es obligatoria.

    Raises:
        ValueError: host no loopback sin ``auth_key``.
    """
    from fastapi import FastAPI

    from inaki.channels.rest.routers.config import router
    from inaki.channels.rest.routers.deps import check_admin_auth
    from inaki.config.wiring import build_config_web

    app = FastAPI(title="Inaki — Config")
    app.state.config_web = build_config_web(daemon=False)
    if es_loopback(host):
        # La dependencia se anula acá, en el composition root; el router queda
        # idéntico al del daemon.
        app.state.admin_auth_key = None
        app.dependency_overrides[check_admin_auth] = lambda: None
    else:
        if not auth_key:
            raise ValueError(
                f"--host {host} expone la UI fuera de esta máquina y eso exige auth: "
                "configurá admin.auth_key en global.yaml (la misma key del admin server) "
                "o dejá el default 127.0.0.1."
            )
        app.state.admin_auth_key = auth_key
    app.include_router(router)
    return app


def web(
    port: int = typer.Option(6498, "--port", help="Puerto de la UI."),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Interfaz de escucha. Loopback (default) corre SIN auth; cualquier otra "
        "(0.0.0.0, una IP de la LAN) exige admin.auth_key, como el admin server.",
    ),
) -> None:
    """UI web de config: la config EFECTIVA con origen, editable por capa.

    Standalone: no necesita el daemon. Lo que guardás se valida con el mismo
    loader del arranque y aplica al próximo arranque (o `inaki reload`). Con el
    daemon corriendo, la misma UI vive en el admin server: `/admin/config/ui`.
    """
    import uvicorn

    auth_key: str | None = None
    if not es_loopback(host):
        from inaki.config import load_global_config
        from inaki.config.boundary import borde_de_config

        config_dir, _ = _common.resolve_dirs()
        with borde_de_config(str(config_dir)):
            global_cfg, _ = load_global_config(config_dir)
        auth_key = global_cfg.admin.auth_key
    try:
        app = construir_app(host=host, auth_key=auth_key)
    except ValueError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=1) from exc
    modo = "sin auth (loopback)" if es_loopback(host) else "con X-Admin-Key (admin.auth_key)"
    typer.echo(f"UI de config en http://{host}:{port}/admin/config/ui — {modo}. Ctrl+C para salir.")
    uvicorn.run(app, host=host, port=port, log_level="warning")
