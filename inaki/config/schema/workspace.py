"""Bloque ``workspace``: raíz de ficheros y contención de las file tools.

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from inaki.config.schema._base import ExpandedPath, _ConfigBaseModel

ContainmentMode = Literal["strict", "warn", "off"]


class WorkspaceConfig(_ConfigBaseModel):
    """
    Workspace sobre el que operan las tools de filesystem.

    Define el sandbox de ``read_file``, ``write_file``, ``patch_file`` y
    ``edit_file``: dónde resuelven los paths relativos y qué pasa cuando el LLM
    pide uno que se sale. Bloque per-agente — darle a cada agente su propio
    ``path`` los mantiene separados en disco.
    """

    path: ExpandedPath = "~/inaki-workspace"
    """Directorio raíz contra el que las tools de filesystem resuelven los paths relativos.

    Es también la frontera que aplica ``containment``. ``~`` se expande al cargar
    la config."""

    containment: ContainmentMode = "strict"
    """Qué hacer cuando un path resuelto se sale del workspace (absoluto o vía ``..``).

    - ``strict`` (default, recomendado en producción) → aborta con
      ``WorkspaceEscapeError``; la tool nunca toca el fichero.
    - ``warn`` → permite el acceso y deja un WARNING en el log.
    - ``off`` → sin chequeo; las tools pueden leer y escribir en cualquier lado
      al que llegue el proceso."""

    def model_post_init(self, __context: object) -> None:
        # Expand ~ in the default value (BeforeValidator no corre en defaults de clase).
        object.__setattr__(self, "path", str(Path(self.path).expanduser()))
