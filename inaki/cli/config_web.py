"""``inaki config web`` — la UI de config servida standalone.

Vive en el composition root y no en ``inaki/config/cli.py`` porque MONTA un
canal (el router de ``inaki/channels/rest``) sobre un FastAPI propio, y
``inaki/config`` solo conoce el kernel y ``shared``: el contrato de
``import-linter`` lo prohíbe, y con razón — config es un módulo, no un
ensamblador. Se registra sobre ``config_app`` desde ``inaki/cli/__init__.py``.
"""

from __future__ import annotations

import typer


def web(
    port: int = typer.Option(6498, "--port", help="Puerto local de la UI."),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Interfaz de escucha. Loopback por default: esta UI corre SIN auth, con el mismo "
        "nivel de confianza que editar el YAML a mano. Exponerla en la LAN es decisión tuya.",
    ),
) -> None:
    """UI web de config: la config EFECTIVA con origen, editable por capa.

    Standalone: no necesita el daemon. Lo que guardás se valida con el mismo
    loader del arranque y aplica al próximo arranque (o `inaki reload`). Con el
    daemon corriendo, la misma UI vive en el admin server: `/admin/config/ui`.
    """
    import uvicorn
    from fastapi import FastAPI

    from inaki.channels.rest.routers.config import router
    from inaki.channels.rest.routers.deps import check_admin_auth
    from inaki.config.wiring import build_config_web

    app = FastAPI(title="Inaki — Config")
    app.state.config_web = build_config_web(daemon=False)
    # Loopback en la máquina del operador: sin key. La dependencia se anula acá,
    # en el composition root, y el router queda idéntico al del daemon.
    app.dependency_overrides[check_admin_auth] = lambda: None
    app.include_router(router)
    typer.echo(f"UI de config en http://{host}:{port}/admin/config/ui  (Ctrl+C para salir)")
    uvicorn.run(app, host=host, port=port, log_level="warning")
