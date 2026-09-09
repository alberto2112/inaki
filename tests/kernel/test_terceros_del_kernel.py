"""El kernel solo usa terceros del allowlist — la regla que ``lint-imports`` no expresa.

La ley entre paquetes (qué módulo puede importar a cuál) vive en ``pyproject.toml``
→ ``[tool.importlinter]``. Lo que import-linter no puede decir es "solo ESTOS
terceros": el kernel (``inaki/kernel``) se limita a ``pydantic``, ``croniter`` y
``numpy`` para que el turno no arrastre clientes HTTP, SDKs ni drivers.

Cubre imports top-level, locales (dentro de funciones) y TYPE_CHECKING — la regla
aplica a TODOS: un import "solo de tipos" también acopla al detalle de
implementación.

Es tipo *ratchet*: las violaciones preexistentes a la auditoría del 2026-06-11 están
declaradas en ``DEUDA_TERCEROS_KERNEL`` y no fallan, pero (a) cualquier violación
NUEVA falla al instante, y (b) saldar una entrada sin borrarla de la lista también
falla — la deuda solo puede achicarse, nunca crecer. Quedó vacía el 2026-06-11.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

RAIZ_REPO = Path(__file__).resolve().parents[2]
KERNEL_DIR = RAIZ_REPO / "inaki" / "kernel"


# Paquetes first-party del repo — no son "terceros" para la regla 2 (los cruces
# entre capas locales ya los cubren las reglas 1 y 3).
PAQUETES_LOCALES = frozenset({"inaki", "ext", "tests"})

ALLOWLIST_TERCEROS_KERNEL = frozenset(
    {
        "pydantic",
        "croniter",
        # Oficializado en la auditoría 2026-06-11: embeddings faciales de 512
        # floats + cosine similarity en Pi 5 — en Python puro sería inviable.
        "numpy",
    }
)

# ---------------------------------------------------------------------------
# Deuda conocida (auditoría 2026-06-11) — ratchet: solo puede achicarse.
# Cada entrada es (path relativo al repo, módulo importado).
# ---------------------------------------------------------------------------

# Regla 2 — deuda saldada el 2026-06-11 (CryptoService eliminado; la api_key de
# web_search vive en config/tool_config.yaml). Mantener vacío.
DEUDA_TERCEROS_KERNEL: frozenset[tuple[str, str]] = frozenset()


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


def _assert_ratchet(
    actuales: set[tuple[str, str]], deuda: frozenset[tuple[str, str]], regla: str
) -> None:
    """Falla si hay violaciones nuevas O entradas de deuda ya saldadas sin borrar."""
    nuevas = actuales - deuda
    saldadas = deuda - actuales

    mensajes: list[str] = []
    if nuevas:
        mensajes.append(
            f"Violaciones NUEVAS de la regla [{regla}] — no agregues entradas a la "
            "deuda, resolvé el acoplamiento (ver docstring del módulo):\n  "
            + "\n  ".join(f"{archivo} importa '{modulo}'" for archivo, modulo in sorted(nuevas))
        )
    if saldadas:
        mensajes.append(
            f"Deuda SALDADA de la regla [{regla}] — buenísimo, ahora borrá estas "
            "entradas de la lista DEUDA_* para que el ratchet no retroceda:\n  "
            + "\n  ".join(f"({archivo!r}, {modulo!r})" for archivo, modulo in sorted(saldadas))
        )
    assert not mensajes, "\n\n".join(mensajes)


def test_el_kernel_solo_usa_terceros_del_allowlist() -> None:
    """Regla 2: terceros en core/ limitados a ALLOWLIST_TERCEROS_KERNEL.

    Sumar una librería al allowlist es una decisión de arquitectura: el dominio
    entero queda acoplado a ella. Si de verdad hace falta, agregala con un
    comentario que justifique el porqué (como numpy) y documentala en CLAUDE.md.
    """
    assert KERNEL_DIR.is_dir(), f"No se encontró el directorio core/ en {KERNEL_DIR}"

    actuales: set[tuple[str, str]] = set()
    for archivo in sorted(KERNEL_DIR.rglob("*.py")):
        rel = archivo.relative_to(RAIZ_REPO).as_posix()
        for modulo in _imports_de(archivo):
            raiz = modulo.split(".")[0]
            if raiz in sys.stdlib_module_names or raiz in PAQUETES_LOCALES:
                continue
            if raiz not in ALLOWLIST_TERCEROS_KERNEL:
                actuales.add((rel, raiz))

    _assert_ratchet(actuales, DEUDA_TERCEROS_KERNEL, "core solo terceros del allowlist")
