"""Módulo de configuración de Inaki: schema, carga en 2 capas, merge, origen y edición.

Punto de import único (``from inaki.config import GlobalConfig, load_global_config``).
Adentro:

- ``schema/``: modelos Pydantic, una sección por área (``app``, ``llm``, ``telegram``...).
- ``loader``: lectura de ``global.yaml`` + ``agents/{id}.yaml``, merge, validación,
  bootstrap del home y migraciones automáticas.
- ``merge``: el motor de merge ÚNICO (dict⊕dict funde, lista reemplaza, ``null``
  pisa, sentinel borra) — lo usan carga, edición, config efectiva y sub-agentes.
- ``home``: resolución del home de la instancia (``--home`` / ``INAKI_HOME``).
- ``introspection`` / ``docs``: lo que el schema sabe de sí mismo (defaults,
  secretos) y el generador de ``config-reference.md`` / ``global.example.yaml``.
- ``ports`` + ``adapters/yaml_repository``: lectura/escritura de las capas crudas
  preservando comentarios.
- ``use_cases/``: config efectiva con origen, edición de capas, agentes y providers.
- ``tools/config_tool``: la tool con la que el agente consulta su config en memoria.
- ``boundary``: el borde ÚNICO donde un ``ConfigError`` se vuelve un mensaje accionable.
- ``cli``: ``inaki config show``.

Ley de dependencias (``lint-imports``): este módulo solo conoce ``inaki.shared`` y
los ports de ``core``; ni ``adapters`` ni ``infrastructure`` ni el composition root.
"""

from __future__ import annotations

from inaki.config.loader import *  # noqa: F401,F403
from inaki.config.schema import *  # noqa: F401,F403
