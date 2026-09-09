"""Bloque ``app``: arranque del proceso (logging, debug, extensiones, agente por defecto).

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import ConfigDict

from inaki.config.schema._base import RuntimePath, _ConfigBaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------


class AppConfig(_ConfigBaseModel):
    """Arranque del proceso: identidad, logging, agente por defecto y extensiones.

    Bloque SOLO global (``global.yaml`` → ``app:``). No admite override
    per-agente: lo consumen el composition root (``inaki/app/bootstrap.py``) y el
    ``AppContainer`` antes de que exista ningún agente.
    """

    model_config = ConfigDict(validate_default=True)  # RuntimePath en los defaults

    name: str = "Inaki"
    """Nombre de la instancia del asistente.

    DECLARATIVO: ningún componente del runtime lo lee hoy — queda como etiqueta
    del despliegue para el operador. El nombre con el que el agente se presenta
    ante el LLM es ``AgentConfig.name``, no este."""

    log_level: str = "INFO"
    """Nivel mínimo de log del proceso: ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR`` o ``CRITICAL``.

    Se resuelve contra el módulo ``logging`` sin distinguir mayúsculas; un valor
    no reconocido cae a ``INFO`` sin fallar. ``DEBUG`` agrega el detalle de los
    requests al provider, los embeddings y las tool calls."""

    log_format: Literal["console", "json"] = "console"
    """Formato de cada línea de log.

    ``console``: ``HH:MM:SS NIVEL logger: mensaje  clave=valor`` — legible en una
    terminal y en ``journalctl``. ``json``: una línea JSON por evento con ``ts``,
    ``level``, ``logger``, ``msg`` y los campos estructurados del evento — para
    filtrar con ``jq`` o enviar a un colector. En los dos formatos los campos
    ``extra`` de cada log (``chat_id``, ``from_agent_id``, ``resource``...) se
    publican; antes se perdían."""

    debug: bool = False
    """Modo diagnóstico del proceso.

    Activo: el nivel de log sube a ``DEBUG`` (ignora ``log_level``) y cada turno
    deja una traza en ``<home>/debug/turns/<agent_id>.jsonl`` con el prompt
    ensamblado, las tools y skills que eligió el routing, cada respuesta del LLM
    y cada tool call con su resultado (strings largos recortados a 4000
    caracteres). La flag ``inaki --debug`` activa lo mismo para UN arranque sin
    tocar el YAML y gana sobre este campo. Apagado por default: las trazas
    contienen el contenido de las conversaciones."""

    ext_dirs: list[RuntimePath] = ["ext"]
    """Directorios donde se auto-descubren las extensiones de usuario, en orden.

    Cada directorio se escanea buscando ``*/manifest.py``, que declara tools,
    skills y fuentes de knowledge propias. Un path relativo se ancla al home de
    instancia (``<home>/ext`` por defecto; se reancla con ``--home`` /
    ``INAKI_HOME``); un path absoluto se usa tal cual y ``~`` se expande. Un
    directorio declarado que no existe se saltea con un ``WARNING`` en el log:
    nombra un recurso que el operador espera cargado."""

    default_agent: str = "general"
    """Agente que usan los comandos de CLI cuando no se pasa ``--agent``.

    Debe corresponder a un fichero ``agents/{id}.yaml`` existente: nadie lo valida al
    cargar, y un id inexistente hace fallar los comandos de CLI al resolver el
    agente (``AgentNotFoundError``)."""
