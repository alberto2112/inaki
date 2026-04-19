"""
Daemon runner — arranca todos los canales de todos los agentes en un único event loop.

Se ejecuta como servicio systemd. Levanta en paralelo:
  - Un servidor FastAPI/uvicorn por cada agente con canal 'rest'
  - Un bot Telegram por cada agente con canal 'telegram'

Maneja SIGTERM/SIGINT para shutdown gracioso (systemd KillMode=process).
"""

from __future__ import annotations

import asyncio
import logging
import signal

logger = logging.getLogger(__name__)


async def _run_admin_server(app_container, admin_cfg, servers: list) -> None:
    """Arranca el admin server global del daemon."""
    import uvicorn
    from adapters.inbound.rest.admin.app import create_admin_app

    if admin_cfg.auth_key is None:
        logger.warning(
            "Admin auth_key no configurada — endpoints protegidos devolverán 403. "
            "Configurala en global.secrets.yaml: admin.auth_key"
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
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    servers.append(server)
    logger.info("Admin server iniciado en %s:%d", admin_cfg.host, admin_cfg.port)
    await server.serve()


async def _run_rest_server(agent_cfg, container, servers: list) -> None:
    """Arranca un servidor uvicorn para un agente en su puerto configurado."""
    import uvicorn
    from adapters.inbound.rest.app import create_agent_app

    rest_cfg = agent_cfg.channels.get("rest", {})
    host = rest_cfg.get("host", "0.0.0.0")
    port = int(rest_cfg.get("port", 6498))

    app = create_agent_app(agent_cfg, container)
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=True,
    )
    server = uvicorn.Server(config)
    # Desactivamos los signal handlers de uvicorn: los maneja el daemon
    # vía should_exit para un shutdown coordinado de todos los canales.
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    servers.append(server)
    logger.info("REST server iniciado para '%s' en %s:%d", agent_cfg.id, host, port)
    await server.serve()


async def _run_telegram_bot(agent_cfg, container, app_container=None) -> None:
    """Arranca el bot de Telegram para un agente usando la API async nativa de PTB 21+."""
    from adapters.inbound.telegram.bot import TelegramBot

    try:
        bot = TelegramBot(agent_cfg, container)
    except ValueError as exc:
        logger.warning("Telegram bot no iniciado para '%s': %s", agent_cfg.id, exc)
        return

    # Registrar el bot en el gateway para que ChannelSenderAdapter pueda encontrarlo
    if app_container is not None:
        app_container.register_telegram_bot(agent_cfg.id, bot)

    logger.info("Telegram bot iniciando para agente '%s'", agent_cfg.id)

    # python-telegram-bot 21+ ofrece API async nativa via context manager
    async with bot._app:
        await bot._app.start()
        await bot.setup_commands()
        await bot._app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot '%s' en polling", agent_cfg.id)
        try:
            await asyncio.get_running_loop().create_future()  # bloquear hasta cancelación
        except asyncio.CancelledError:
            pass
        finally:
            await bot._app.updater.stop()
            await bot._app.stop()


async def run_daemon(app_container, registry) -> None:
    """
    Arranca todos los canales de todos los agentes en paralelo.
    Cancela graciosamente cuando recibe SIGTERM o SIGINT.
    """
    # TODO: implementar handler de channel_send para daemon (dispatch handler para tareas sin conversación activa)
    # Scheduler startup
    await app_container.startup()

    shutdown_event = asyncio.Event()

    def _handle_signal(*_):
        logger.info("Señal de apagado recibida — iniciando shutdown gracioso")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    tasks: list[asyncio.Task] = []
    uvicorn_servers: list = []

    # Admin server — global, puerto separado
    admin_cfg = app_container.global_config.admin
    admin_task = asyncio.create_task(
        _run_admin_server(app_container, admin_cfg, uvicorn_servers),
        name="admin",
    )
    tasks.append(admin_task)

    # REST servers
    for agent_cfg in registry.agents_with_channel("rest"):
        rest_cfg = agent_cfg.channels.get("rest", {})
        if not rest_cfg.get("auth_key"):
            logger.warning(
                "Agente '%s': channels.rest.auth_key no configurado — servidor REST igualmente levantado sin auth",
                agent_cfg.id,
            )
        try:
            container = app_container.get_agent(agent_cfg.id)
        except Exception as exc:
            logger.error("No se pudo obtener container para '%s': %s", agent_cfg.id, exc)
            continue
        task = asyncio.create_task(
            _run_rest_server(agent_cfg, container, uvicorn_servers),
            name=f"rest:{agent_cfg.id}",
        )
        tasks.append(task)

    # Telegram bots
    for agent_cfg in registry.agents_with_channel("telegram"):
        tg_cfg = agent_cfg.channels.get("telegram", {})
        if not tg_cfg.get("token"):
            logger.warning(
                "Agente '%s': channels.telegram.token no configurado — bot Telegram no levantado",
                agent_cfg.id,
            )
            continue
        try:
            container = app_container.get_agent(agent_cfg.id)
        except Exception as exc:
            logger.error("No se pudo obtener container para '%s': %s", agent_cfg.id, exc)
            continue
        task = asyncio.create_task(
            _run_telegram_bot(agent_cfg, container, app_container),
            name=f"telegram:{agent_cfg.id}",
        )
        tasks.append(task)

    if not tasks:
        logger.warning(
            "No hay canales configurados (ningún agente tiene 'rest' ni 'telegram'). "
            "El daemon no tiene nada que hacer."
        )
        return

    logger.info(
        "Daemon iniciado: %d tarea(s) activa(s): %s",
        len(tasks),
        [t.get_name() for t in tasks],
    )

    # Esperar shutdown o que alguna tarea falle
    shutdown_task = asyncio.create_task(shutdown_event.wait(), name="shutdown")
    done, pending = await asyncio.wait(
        [*tasks, shutdown_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Shutdown gracioso de uvicorn: should_exit = True deja que uvicorn
    # haga su propio teardown del lifespan en lugar de recibir un
    # CancelledError en mitad de starlette.routing.lifespan.
    for server in uvicorn_servers:
        server.should_exit = True

    # Cancelar telegram bots (no tienen protocolo should_exit).
    # Los tasks de uvicorn (rest:* y admin) terminarán por su cuenta
    # cuando should_exit tome efecto, pero igual los esperamos en el gather.
    _uvicorn_task_prefixes = ("rest:", "admin")
    for task in pending:
        if not task.get_name().startswith(_uvicorn_task_prefixes):
            task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    # Reportar si alguna tarea falló antes del shutdown
    for task in done:
        if task.get_name() != "shutdown" and not task.cancelled():
            exc = task.exception()
            if exc:
                logger.error("Tarea '%s' falló con: %s", task.get_name(), exc)

    # Scheduler shutdown
    await app_container.shutdown()

    logger.info("Daemon apagado limpiamente.")
