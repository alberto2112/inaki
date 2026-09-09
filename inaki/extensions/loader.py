"""Descubrimiento de extensiones de usuario: ``<ext_dir>/<nombre>/manifest.py``.

Contrato del manifest (no cambia con el refactor): un módulo Python que declara,
todos opcionales, ``TOOLS`` (clases ``ITool``), ``SKILLS`` (paths de YAML
relativos al directorio de la extensión) y ``KNOWLEDGE_SOURCES`` (factories
``(agent_config, global_config, embedder) -> IKnowledgeSource``).

Este módulo solo DESCUBRE: importa cada manifest y devuelve lo que declara, ya
resuelto y validado en lo que se puede validar sin instanciar nada. Registrar
(instanciar las tools, añadir las skills, construir las fuentes) es trabajo del
composition root, que es quien tiene los registros. Así ``extensions`` no conoce
a ``tools``, ``skills`` ni ``knowledge`` —una extensión es un plugin que aporta a
los tres— y un manifest roto nunca tumba el arranque: se loguea y se salta.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

logger = logging.getLogger(__name__)

KnowledgeSourceFactory = Callable[[Any, Any, Any], Any]
"""``(agent_config, global_config, embedder) -> IKnowledgeSource``."""


@dataclass(frozen=True)
class Extension:
    """Lo que declara un ``manifest.py`` que cargó bien."""

    nombre: str
    directorio: Path
    tools: tuple[type, ...] = ()
    skills: tuple[Path, ...] = field(default_factory=tuple)
    knowledge_sources: tuple[KnowledgeSourceFactory, ...] = ()


def descubrir_extensiones(ext_dirs: Iterable[str]) -> list[Extension]:
    """Recorre ``ext_dirs`` en orden y devuelve una ``Extension`` por manifest cargado.

    - Un directorio declarado que no existe se saltea con ``WARNING``: es un
      recurso que la config nombra y el operador espera ver cargado; callarlo
      convierte "mis extensiones desaparecieron" en un misterio.
    - El padre de cada directorio entra a ``sys.path`` para que los imports
      internos de cada extensión (``from ext.mi_ext.engine import ...``)
      resuelvan; si el paquete ya se importó desde otro directorio, se extiende
      su ``__path__`` en vez de dejar que el primero monopolice el nombre.
    - Un manifest que no importa (sintaxis, dependencia ausente) se loguea con
      ``WARNING`` y se salta; el resto sigue.
    """
    extensiones: list[Extension] = []
    for ext_dir_str in ext_dirs:
        ext_dir = Path(ext_dir_str).expanduser().resolve()
        if not ext_dir.is_dir():
            logger.warning(
                "Extensiones: el directorio declarado en app.ext_dirs no existe: %s "
                "— no se carga ninguna extensión desde ahí",
                ext_dir,
            )
            continue
        _exponer_en_sys_path(ext_dir)
        for manifest_path in sorted(ext_dir.glob("*/manifest.py")):
            nombre = manifest_path.parent.name
            module = _importar_manifest(ext_dir, nombre, manifest_path)
            if module is None:
                continue
            extensiones.append(
                Extension(
                    nombre=nombre,
                    directorio=manifest_path.parent,
                    tools=tuple(getattr(module, "TOOLS", None) or ()),
                    skills=_resolver_skills(nombre, manifest_path.parent, module),
                    knowledge_sources=tuple(getattr(module, "KNOWLEDGE_SOURCES", None) or ()),
                )
            )
    return extensiones


def _exponer_en_sys_path(ext_dir: Path) -> None:
    parent_str = str(ext_dir.parent)
    if parent_str not in sys.path:
        sys.path.insert(0, parent_str)
        logger.debug("sys.path += %s (extensiones en %s)", parent_str, ext_dir.name)
    pkg = sys.modules.get(ext_dir.name)
    if pkg is not None and hasattr(pkg, "__path__") and str(ext_dir) not in list(pkg.__path__):
        pkg.__path__.append(str(ext_dir))
        logger.debug("Extendido %s.__path__ += %s", ext_dir.name, ext_dir)


def _importar_manifest(ext_dir: Path, nombre: str, manifest_path: Path) -> ModuleType | None:
    # ID único para que dos extensiones con el mismo nombre en dirs distintos no colisionen.
    module_id = f"_inaki_ext_{ext_dir.name}_{nombre}_manifest"
    try:
        spec = importlib.util.spec_from_file_location(module_id, manifest_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"No se pudo armar ModuleSpec para {manifest_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:
        logger.warning("Extensión '%s': falló al cargar manifest (%s) — skipping", nombre, exc)
        return None
    return module


def _resolver_skills(nombre: str, directorio: Path, module: ModuleType) -> tuple[Path, ...]:
    skills: list[Path] = []
    for skill_rel in getattr(module, "SKILLS", None) or ():
        skill_path = (directorio / skill_rel).resolve()
        if not skill_path.exists():
            logger.warning("Extensión '%s': skill file no encontrado: %s", nombre, skill_path)
            continue
        skills.append(skill_path)
    return tuple(skills)
