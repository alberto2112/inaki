"""Bootstrap del composition root: config → logging → registry → container → daemon.

Es el ÚNICO lugar donde se encadenan la carga de config (con su borde de errores),
el logging del proceso y la construcción del ``AppContainer``. ``inaki daemon`` lo
invoca; cada reload lo vuelve a invocar desde cero.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from inaki.config.home import get_inaki_home
from inaki.observability import is_debug_enabled, setup_logging


def bootstrap(config_dir: Path, agents_dir: Path):
    """Carga config, logging y registry. Retorna (global_config, registry)."""
    from inaki.config import AgentRegistry, load_global_config
    from inaki.config.boundary import borde_de_config

    with borde_de_config(str(config_dir)):
        global_config, global_raw = load_global_config(config_dir)

    # --debug gana sobre app.debug; con debug el nivel es DEBUG sin importar log_level.
    app_cfg = global_config.app
    nivel = "DEBUG" if is_debug_enabled(app_cfg.debug) else app_cfg.log_level
    setup_logging(nivel, app_cfg.log_format)

    # El registry va DENTRO del borde: los YAML de agente validan acá
    # (`_check_top_level`, shape legacy, unicidad de canal), y su `ConfigError`
    # salía crudo porque el `try` cubría la carga del global y nada más. El
    # mensaje del core era bueno; llegaba enterrado bajo el traceback.
    with borde_de_config(str(agents_dir)):
        registry = AgentRegistry(agents_dir, global_raw)
    if not registry.list_all():
        print(
            f"No hay agentes configurados en {agents_dir}. "
            "Crea un archivo .yaml de agente para comenzar.",
            file=sys.stderr,
        )
        sys.exit(1)

    return global_config, registry


def run_daemon_mode(config_dir: Path, agents_dir: Path, global_config, registry) -> None:
    """Arranca todos los canales en modo servicio systemd.

    Recibe paths + el bootstrap inicial ya hecho. El primer arranque usa el initial
    (sin re-leer config), y cada reload re-invoca ``_bootstrap`` desde cero leyendo
    el contenido actual de ``config_dir`` / ``agents_dir``.
    """
    import logging

    from inaki.app.runner import run_daemon
    from inaki.app.container import AppContainer

    logger = logging.getLogger(__name__)
    logger.info("Iniciando Inaki en modo daemon")

    initial_container = AppContainer(global_config, registry, config_dir=config_dir)

    # Crea ~/.inaki/users/{channel}/ por cada canal configurado en cualquier agente.
    # Lazy + idempotente: cero costo si ya existen. Habilita la convención de
    # contexto per-entidad (ver docs/contexto-por-entidad.md).
    from inaki.config import ensure_user_channel_dirs

    ensure_user_channel_dirs(get_inaki_home(), registry.list_all())

    def bootstrap_fn():
        gc, reg = bootstrap(config_dir, agents_dir)
        ensure_user_channel_dirs(get_inaki_home(), reg.list_all())
        return AppContainer(gc, reg, config_dir=config_dir), reg

    asyncio.run(run_daemon(bootstrap_fn, initial=(initial_container, registry)))
