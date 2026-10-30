"""Bootstrap del composition root: config → logging → registry → container → daemon.

Es el ÚNICO lugar donde se encadenan la carga de config (con su borde de errores),
el logging del proceso y el ensamblado del ``HarnessRuntime``. ``inaki daemon`` lo
invoca; cada reload lo vuelve a invocar desde cero.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from inaki.config.home import get_inaki_home
from inaki.observability import is_debug_enabled, setup_logging

if TYPE_CHECKING:
    from inaki.app.runtime import HarnessRuntime
    from inaki.config.runtime_overrides import OverridesDeRuntime


def bootstrap(
    config_dir: Path,
    agents_dir: Path,
    overrides: OverridesDeRuntime | None = None,
    *,
    codigo_de_salida: int | None = None,
):
    """Carga config, logging y registry. Retorna (global_config, registry).

    ``overrides`` es la capa de overrides en memoria (``config set``): el loader la apila
    después de los YAML. ``None`` (o un store vacío) deja el comportamiento de siempre.

    ``codigo_de_salida`` es el exit de una config rechazada: ``EXIT_CONFIG`` (la unidad no
    reinicia) por defecto; el reload pasa ``EXIT_REINICIAR`` (ver ``inaki.config.boundary``).
    """
    from inaki.config import AgentRegistry, load_global_config
    from inaki.config.boundary import EXIT_CONFIG, borde_de_config

    codigo = EXIT_CONFIG if codigo_de_salida is None else codigo_de_salida
    with borde_de_config(str(config_dir), codigo_de_salida=codigo):
        global_config, global_raw = load_global_config(
            config_dir, runtime=overrides.capa_global() if overrides else None
        )

    # --debug gana sobre app.debug; con debug el nivel es DEBUG sin importar log_level.
    app_cfg = global_config.app
    nivel = "DEBUG" if is_debug_enabled(app_cfg.debug) else app_cfg.log_level
    setup_logging(nivel, app_cfg.log_format)

    # El registry va DENTRO del borde: los YAML de agente validan acá
    # (`_check_top_level`, shape legacy, unicidad de canal), y su `ConfigError`
    # salía crudo porque el `try` cubría la carga del global y nada más. El
    # mensaje del core era bueno; llegaba enterrado bajo el traceback.
    with borde_de_config(str(agents_dir), codigo_de_salida=codigo):
        registry = AgentRegistry(
            agents_dir,
            global_raw,
            runtime_por_agente=overrides.capas_por_agente() if overrides else None,
        )
    if not registry.list_all():
        print(
            f"No hay agentes configurados en {agents_dir}. "
            "Crea un archivo .yaml de agente para comenzar.",
            file=sys.stderr,
        )
        # Es config del operador como cualquier otra: un reinicio no crea el agente.
        sys.exit(codigo)

    return global_config, registry


def avisos_post_arranque(
    overrides: OverridesDeRuntime,
) -> Callable[[HarnessRuntime], Awaitable[None]]:
    """Hook ``al_arrancar``: entrega al chat de origen los avisos que ``config set`` dejó.

    Corre tras ``_start_channels``, con el runtime NUEVO: manda por el outbound del
    agente (el mismo egress único que usa ``POST /admin/send``). Un fallo de envío —o un
    destino que ya no existe— es un WARNING que nombra el destino: el cambio ya está
    aplicado y el aviso es cortesía. Cada aviso se entrega una sola vez
    (``tomar_avisos`` vacía la lista).

    El aviso NO se persiste (``record_history=False``): es feedback del arnés al
    operador, no algo que dijo el agente. En ``history.db`` el LLM lo leería como
    una respuesta propia sobre un cambio de modelo que no recuerda haber hecho.
    """
    from inaki.kernel.domain.outbound_kind import OutboundKind

    log = logging.getLogger(__name__)

    async def hook(app_container: HarnessRuntime) -> None:
        for aviso in overrides.tomar_avisos():
            destino = f"{aviso.canal}:{aviso.chat_id} (agente '{aviso.agent_id}')"
            try:
                outbound = app_container.get_agent(aviso.agent_id).channel_outbound_registry.get(
                    aviso.canal
                )
                await outbound.send(
                    chat_id=aviso.chat_id,
                    kind=OutboundKind.TEXT,
                    text=aviso.texto,
                    record_history=False,
                )
            except Exception as exc:  # noqa: BLE001 — cortesía: nunca corta el arranque
                log.warning(
                    "No se pudo entregar el aviso de config (set) a %s: %s",
                    destino,
                    exc,
                    exc_info=True,
                )

    return hook


def avisos_de_background() -> Callable[[HarnessRuntime], Awaitable[None]]:
    """Hook ``al_arrancar``: avisa a los padres de las delegaciones que el reinicio cortó.

    Corre tras ``_start_channels``, con el runtime NUEVO (REQ-BGD-11), nunca dentro de
    ``startup()``: el aviso sale por los canales y estos todavía no estaban arriba. Solo
    PROGRAMA el aviso y vuelve: dispara un turno completo del agente padre (llamada al LLM,
    10-60 s) y el runner espera este hook ANTES de levantar las tareas de los canales —
    esperarlo dejaría al bot sordo todo ese rato.
    """

    async def hook(app_container: HarnessRuntime) -> None:
        app_container.background_queue.programar_avisos_post_arranque()

    return hook


def componer_al_arrancar(
    *hooks: Callable[[HarnessRuntime], Awaitable[None]],
) -> Callable[[HarnessRuntime], Awaitable[None]]:
    """Un solo hook ``al_arrancar`` que corre varios, en orden, cada uno AISLADO.

    El runner acepta un único hook y si levanta lo loguea y sigue; con dos hooks a secas,
    uno roto dejaría sin correr al siguiente. Acá cada uno va en su propio ``try``: un
    aviso de config que falla no apaga el aviso de las delegaciones cortadas (ni al revés).
    """
    log = logging.getLogger(__name__)

    async def hook(app_container: HarnessRuntime) -> None:
        for h in hooks:
            try:
                await h(app_container)
            except Exception:  # noqa: BLE001 — un aviso fallido no tumba un daemon sano
                log.exception("Un hook al_arrancar falló (%s) — se sigue con el resto", h)

    return hook


def run_daemon_mode(config_dir: Path, agents_dir: Path, global_config, registry) -> None:
    """Arranca todos los canales en modo servicio systemd.

    Recibe paths + el bootstrap inicial ya hecho. El primer arranque usa el initial
    (sin re-leer config), y cada reload re-invoca ``_bootstrap`` desde cero leyendo
    el contenido actual de ``config_dir`` / ``agents_dir``.
    """
    import logging

    from inaki.app.runner import run_daemon
    from inaki.app.assembly import ensamblar
    from inaki.config.boundary import EXIT_REINICIAR, borde_de_config
    from inaki.config.runtime_overrides import OverridesDeRuntime

    logger = logging.getLogger(__name__)
    logger.info("Iniciando Inaki en modo daemon")

    # ``ensamblar`` también rechaza config (provider inexistente, credencial
    # faltante): lo que el loader no puede saber lo sabe la factory. Va dentro del
    # MISMO borde que la carga, en el arranque y en cada reload — si no, ese
    # ``ConfigError`` sale enterrado bajo el traceback.
    # El store de ``config set`` es estado del PROCESO: nace acá, UNA vez, y sobrevive al
    # loop de reload (que reconstruye todo lo demás). El arranque y cada re-bootstrap
    # reciben este mismo objeto; si el proceso muere, muere con él (nada va a disco).
    overrides = OverridesDeRuntime()

    with borde_de_config(str(config_dir)):
        initial = ensamblar(global_config, registry, config_dir=config_dir, overrides=overrides)

    # Crea ~/.inaki/users/{channel}/ por cada canal configurado en cualquier agente.
    # Lazy + idempotente: cero costo si ya existen. Habilita la convención de
    # contexto per-entidad (ver docs/contexto-por-entidad.md).
    from inaki.config import ensure_user_channel_dirs

    ensure_user_channel_dirs(get_inaki_home(), registry.list_all())

    # El reload sale con EXIT_REINICIAR, no EXIT_CONFIG: este proceso puede traer overrides
    # que el disco no tiene, y el reinicio leyendo disco puede arrancar sano. Si no, el
    # arranque limpio sale con EXIT_CONFIG y la unidad se para ahí (sin loop).
    def bootstrap_fn():
        gc, reg = bootstrap(
            config_dir, agents_dir, overrides=overrides, codigo_de_salida=EXIT_REINICIAR
        )
        ensure_user_channel_dirs(get_inaki_home(), reg.list_all())
        with borde_de_config(str(config_dir), codigo_de_salida=EXIT_REINICIAR):
            return ensamblar(gc, reg, config_dir=config_dir, overrides=overrides), reg

    asyncio.run(
        run_daemon(
            bootstrap_fn,
            initial=(initial, registry),
            al_arrancar=componer_al_arrancar(
                avisos_post_arranque(overrides), avisos_de_background()
            ),
        )
    )
