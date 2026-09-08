"""
Daemon runner — arranca todos los canales de todos los agentes en un único event loop.

Se ejecuta como servicio systemd. Levanta en paralelo:
  - Un admin server FastAPI/uvicorn (puerto global único — toda la superficie
    REST vive acá, ruteada por agent_id)
  - Los canales del container (``IChannel``: hoy un ``TelegramChannel`` por agente
    con canal telegram), sin saber cuál es cuál

Maneja SIGTERM/SIGINT para shutdown gracioso (systemd KillMode=process).
También soporta reload in-place: cuando alguien señaliza ``app_container.reloader``
(vía ``inaki reload``, ``POST /admin/reload`` o ``/reload`` en Telegram), el runner
cierra todos los canales, ejecuta ``app_container.shutdown()``, re-bootstrappea config
y vuelve a levantar todo.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import nullcontext
from typing import TYPE_CHECKING, Callable

from inaki.observability import startup_event

if TYPE_CHECKING:
    from inaki.config import AgentRegistry
    from infrastructure.container import AppContainer

logger = logging.getLogger(__name__)


# Tipo del factory que produce un (AppContainer, AgentRegistry) en cada iteración
# del loop de reload. Se invoca al primer arranque y otra vez por cada reload.
BootstrapFn = Callable[[], tuple["AppContainer", "AgentRegistry"]]


async def _run_admin_server(app_container, admin_cfg, servers: list) -> None:
    """Arranca el admin server global del daemon."""
    import uvicorn

    from adapters.inbound.rest.admin.app import create_admin_app

    if admin_cfg.auth_key is None:
        logger.warning(
            "Admin auth_key no configurada — endpoints protegidos devolverán 403. "
            "Configurala en global.yaml: admin.auth_key"
        )

    app = create_admin_app(app_container, admin_auth_key=admin_cfg.auth_key)
    config = uvicorn.Config(
        app,
        host=admin_cfg.host,
        port=admin_cfg.port,
        log_level="info",
        access_log=True,
    )
    server = uvicorn.Server(config)
    # Desactivamos la captura de signals de uvicorn: los maneja el daemon
    # vía should_exit para un shutdown coordinado de todos los canales.
    # En uvicorn >= 0.34 el hook viejo `install_signal_handlers` fue reemplazado
    # por el context manager `capture_signals`, que `serve()` siempre invoca
    # como `with self.capture_signals(): await self._serve(...)`. Sustituirlo
    # por `nullcontext` lo neutraliza sin tocar el flow de `serve()`.
    server.capture_signals = nullcontext  # type: ignore[method-assign,assignment]
    servers.append(server)
    logger.info("Admin server iniciado en %s:%d", admin_cfg.host, admin_cfg.port)
    await server.serve()


def _build_channel_tasks(app_container, registry) -> tuple[list[asyncio.Task], list]:
    """Construye las tasks de larga duración de una iteración del runner (hoy: el admin server).

    Se llama una vez por arranque y otra vez por cada reload. Los canales de
    mensajería NO son tasks: son ``IChannel`` con ``start()``/``stop()`` (ver
    ``_start_channels``).
    """
    tasks: list[asyncio.Task] = []
    uvicorn_servers: list = []

    # Admin server — global, puerto único. Toda la superficie REST (chat, tools,
    # send, gestión) vive acá, ruteada por agent_id.
    admin_cfg = app_container.global_config.admin
    admin_task = asyncio.create_task(
        _run_admin_server(app_container, admin_cfg, uvicorn_servers),
        name="admin",
    )
    tasks.append(admin_task)
    return tasks, uvicorn_servers


async def _start_channels(app_container) -> list:
    """Arranca cada ``IChannel`` del container; uno que falle no tumba a los demás.

    Devuelve los que arrancaron (son los que hay que detener después). El fallo
    queda como ``startup.resource`` con ``status=error`` — el daemon sigue con lo
    que sí levantó, y el operador tiene la línea que lo explica.
    """
    arrancados: list = []
    for canal in app_container.channels:
        try:
            await canal.start()
            arrancados.append(canal)
        except Exception as exc:  # noqa: BLE001 — un canal roto no apaga el daemon
            startup_event(logger, f"channel:{canal.name}", status="error", reason=str(exc))
            logger.exception("El canal '%s' no arrancó", canal.name)
    return arrancados


async def _stop_channels(canales: list) -> None:
    for canal in reversed(canales):
        try:
            await canal.stop()
        except Exception:  # noqa: BLE001
            logger.exception("Error deteniendo el canal '%s'", canal.name)


async def _shutdown_iteration(
    tasks: list[asyncio.Task],
    pending: set[asyncio.Task],
    done: set[asyncio.Task],
    uvicorn_servers: list,
    app_container,
    canales: list | None = None,
) -> None:
    """Cierra una iteración del runner: canales, uvicorn graceful, tasks, app_container.shutdown."""
    # Primero los canales: dejan de recibir mensajes antes de que caiga lo que
    # los atiende (scheduler, cola de background).
    await _stop_channels(canales or [])

    # Shutdown gracioso de uvicorn: should_exit = True deja que uvicorn
    # haga su propio teardown del lifespan en lugar de recibir un
    # CancelledError en mitad de starlette.routing.lifespan.
    for server in uvicorn_servers:
        server.should_exit = True

    # Cancelar las tasks restantes (el admin termina solo cuando should_exit
    # toma efecto, pero igual lo esperamos en el gather).
    for task in pending:
        if task.get_name() != "admin":
            task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    # Reportar si alguna tarea falló antes del shutdown (excluye señales internas)
    _internal_names = {"shutdown", "reload"}
    for task in done:
        if task.get_name() not in _internal_names and not task.cancelled():
            exc = task.exception()
            if exc:
                logger.error("Tarea '%s' falló con: %s", task.get_name(), exc)

    # Scheduler shutdown
    await app_container.shutdown()


async def run_daemon(
    bootstrap_fn: BootstrapFn,
    initial: tuple["AppContainer", "AgentRegistry"] | None = None,
) -> None:
    """
    Arranca todos los canales de todos los agentes en paralelo. Loop de reload-aware:
    cuando ``app_container.reloader`` se dispara, cierra todo, re-bootstrappea config y
    vuelve a levantar el ciclo. Termina solo ante SIGTERM/SIGINT o si no hay canales.

    Args:
        bootstrap_fn: factory que produce ``(AppContainer, AgentRegistry)``. Se invoca en
            cada reload (NO en la primera iter si ``initial`` está presente).
        initial: tupla pre-construida ``(AppContainer, AgentRegistry)`` para usar en la
            primera iter. Permite que el caller valide config antes de entrar al runner
            sin pagar el costo del bootstrap dos veces.
    """
    shutdown_event = asyncio.Event()

    def _handle_signal(*_):
        logger.info("Señal de apagado recibida — iniciando shutdown gracioso")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    iteration = 0
    while True:
        iteration += 1
        if iteration == 1 and initial is not None:
            app_container, registry = initial
        else:
            if iteration > 1:
                logger.info("Daemon recargando — re-bootstrap (iter %d)", iteration)
            try:
                app_container, registry = bootstrap_fn()
            except Exception as exc:
                logger.exception("Bootstrap falló en iter %d: %s", iteration, exc)
                return

        await app_container.startup()
        canales = await _start_channels(app_container)
        tasks, uvicorn_servers = _build_channel_tasks(app_container, registry)

        logger.info(
            "Daemon iniciado: %d tarea(s) activa(s): %s",
            len(tasks),
            [t.get_name() for t in tasks],
        )

        shutdown_task = asyncio.create_task(shutdown_event.wait(), name="shutdown")
        reload_task = asyncio.create_task(app_container.reloader.wait_for_reload(), name="reload")

        done, pending = await asyncio.wait(
            [*tasks, shutdown_task, reload_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        await _shutdown_iteration(tasks, pending, done, uvicorn_servers, app_container, canales)

        if app_container.reloader.was_triggered() and not shutdown_event.is_set():
            logger.info("Reload solicitado — recargando config y canales")
            continue

        break

    logger.info("Daemon apagado limpiamente.")
