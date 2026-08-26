"""Fachada de configuración de Inaki — punto de import único.

El schema (modelos Pydantic) vive en ``config_schema`` y la carga en
``config_loader``; la semántica de merge la define el motor del dominio
(``core/domain/config_merge``), del que ``config_loader`` reexporta alias.

Este módulo los reexporta para preservar el contrato histórico
``from infrastructure.config import X`` sin que el resto del código tenga que
conocer el split. NO agregar lógica acá: schema → config_schema,
carga → config_loader.

Reexporta solo la API PÚBLICA. Durante un tiempo arrastró además 16 símbolos
privados "para preservar el contrato histórico"; al revisarlos, 15 no los
importaba nadie fuera de esta fachada. Si necesitás un interno, importalo de su
módulo de origen: que un privado viaje por la fachada lo convierte de facto en
público, y nadie se entera de que lo es.
"""

from __future__ import annotations

from infrastructure.config_schema import *  # noqa: F401,F403
from infrastructure.config_loader import *  # noqa: F401,F403
