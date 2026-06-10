"""Test de arquitectura — la regla hexagonal del CLAUDE.md, ejecutable.

``core/`` NUNCA importa de ``adapters/`` ni de ``infrastructure/``. Solo se
permiten stdlib, third-party (pydantic, croniter) e imports de ``core/``.
Cubre imports top-level, locales (dentro de funciones) y TYPE_CHECKING —
la regla aplica a TODOS: un import "solo de tipos" también acopla el dominio
al detalle de implementación.

Si este test te falla: el símbolo que necesitás o bien es lógica de dominio
mal ubicada (movela a core/), o bien es un detalle de infraestructura que el
use case no debería conocer (definí un port/Protocol en core/ports/ o un
settings VO en core/domain/value_objects/agent_settings.py y mapealo en
infrastructure/container.py).
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parents[2] / "core"
CAPAS_PROHIBIDAS = ("adapters", "infrastructure")


def _imports_de(archivo: Path) -> list[str]:
    """Extrae todos los módulos importados en el archivo (ast — inmune a comentarios)."""
    tree = ast.parse(archivo.read_text(encoding="utf-8"))
    modulos: list[str] = []
    for nodo in ast.walk(tree):
        if isinstance(nodo, ast.Import):
            modulos.extend(alias.name for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.level == 0:
            modulos.append(nodo.module)
    return modulos


def test_core_no_importa_adapters_ni_infrastructure() -> None:
    assert CORE_DIR.is_dir(), f"No se encontró el directorio core/ en {CORE_DIR}"

    violaciones: list[str] = []
    for archivo in sorted(CORE_DIR.rglob("*.py")):
        for modulo in _imports_de(archivo):
            raiz = modulo.split(".")[0]
            if raiz in CAPAS_PROHIBIDAS:
                rel = archivo.relative_to(CORE_DIR.parent)
                violaciones.append(f"{rel} importa '{modulo}'")

    assert not violaciones, (
        "Violación de la regla hexagonal — core/ no puede importar de "
        "adapters/ ni infrastructure/:\n  " + "\n  ".join(violaciones)
    )
