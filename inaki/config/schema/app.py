"""Bloque ``app``: arranque del proceso (logging, debug, extensiones, agente por defecto).

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

import logging
from typing import Literal

from inaki.config.schema._base import ExpandedPathList, _ConfigBaseModel

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

    ext_dirs: ExpandedPathList = ["ext", "~/.inaki/ext"]
    """Directorios donde se auto-descubren las extensiones de usuario, en orden.

    Cada directorio se escanea buscando ``*/manifest.py``, que registra tools,
    skills y fuentes de knowledge propias. Los paths relativos se resuelven
    contra el cwd del proceso; ``~`` se expande al cargar la config. Un
    directorio inexistente se saltea sin error."""

    default_agent: str = "general"
    """Agente que usan los comandos de CLI cuando no se pasa ``--agent``.

    Debe corresponder a un fichero ``agents/{id}.yaml`` existente: nadie lo valida al
    cargar, y un id inexistente hace fallar los comandos de CLI al resolver el
    agente (``AgentNotFoundError``)."""
