"""Bloque ``scheduler``: tareas programadas (harness-global).

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

from pydantic import ConfigDict

from inaki.config.schema._base import RuntimePath, _ConfigBaseModel
from inaki.config.schema.channels import ChannelFallbackConfig


class SchedulerConfig(_ConfigBaseModel):
    """Motor de tareas programadas: cron, one-shots, reintentos y routing de la salida.

    Recurso HARNESS-GLOBAL: se declara SOLO en ``global.yaml`` y el
    El ensamblador construye una única instancia compartida por todos los
    agentes — no hay ni puede haber un scheduler per-agente. Para aislar
    agendas hay que levantar otra instancia del arnés con su propio
    ``--home`` / ``INAKI_HOME``.

    Solo corre bajo ``inaki daemon``: en la CLI interactiva no hay proceso vivo
    que dispare nada. Los límites de acá (``max_retries``, ``max_tasks_per_agent``,
    ``output_truncation_size``) son las barandas contra un agente que programa
    de más o contra una tarea que falla en loop.
    """

    model_config = ConfigDict(validate_default=True)  # RuntimePath en los defaults

    enabled: bool = True
    """Kill-switch del scheduler. ``False`` = el daemon no arranca el loop de tareas.

    Con ``False`` tampoco se reconcilian las tareas builtin (consolidación,
    reconciliación, dedup de caras): quedan declaradas pero nadie las dispara."""

    db_filename: RuntimePath = "data/scheduler.db"
    """Fichero SQLite con las tareas programadas y su estado de ejecución.

    Relativo al home de instancia; se reancla con ``--home`` / ``INAKI_HOME``. Un
    path absoluto se usa tal cual. Es el mismo fichero que lee ``inaki scheduler``
    desde la CLI."""

    fallback_log_filename: RuntimePath = "data/scheduler-fallback.log"
    """Fallback de último recurso del router de dispatch (cascada). Relativo al home de
    instancia; se reancla con ``--home`` / ``INAKI_HOME``. El composition root lo envuelve
    en ``file://`` y lo inyecta al ``ChannelRouter`` (por privacidad, bajo ``<home>/data/``)."""
    max_retries: int = 3
    """Reintentos de una tarea que falla, ADEMÁS del intento inicial.

    Con el default ``3``, una tarea rota se ejecuta hasta 4 veces. ``0`` =
    un solo intento. Los negativos se saturan a ``0``."""

    retry_backoff_seconds: float = 10.0
    """Base de la espera entre reintentos, en segundos. La progresión es LINEAL.

    El intento N espera ``retry_backoff_seconds * N``: con el default ``10.0``,
    las esperas son 10s, 20s, 30s. ``0`` reintenta sin pausa. Los negativos se
    saturan a ``0``."""

    max_tasks_per_agent: int = 20
    """Techo de tareas ACTIVAS (pending o running) que un agente puede tener a la vez.

    Al llegar al límite, crear otra falla con ``TooManyActiveTasksError`` en vez
    de aceptarla — es la baranda contra un LLM que programa en loop. No cuenta
    las tareas ya completadas ni las canceladas, y no aplica a las tareas sin
    agente creador. El mínimo efectivo es ``1``."""

    output_truncation_size: int = 65536
    """Truncación (en chars) del output de una tarea al guardarlo en su registro de ejecución.

    Acota lo que un ``shell_exec`` verborrágico puede escribir en la DB del
    scheduler. Solo afecta a la copia persistida."""

    channel_fallback: ChannelFallbackConfig = ChannelFallbackConfig()
    """Adónde va la salida de una tarea cuyo canal destino no tiene sink vivo.

    Agrupa el ``default`` global y los ``overrides`` por tipo de canal. Sin
    configurar, todo cae al ``fallback_log_filename``."""
