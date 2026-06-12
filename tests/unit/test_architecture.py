"""Tests de arquitectura — las reglas hexagonales del CLAUDE.md, ejecutables.

Tres reglas:

1. ``core/`` NUNCA importa de ``adapters/`` ni de ``infrastructure/``.
2. ``core/`` solo usa terceros del allowlist (pydantic, croniter, numpy).
3. ``adapters/`` NUNCA importa de ``infrastructure/`` — la dirección documentada
   es ``adapters → core ← infrastructure``, nunca al revés.

Cubre imports top-level, locales (dentro de funciones) y TYPE_CHECKING — la regla
aplica a TODOS: un import "solo de tipos" también acopla al detalle de
implementación.

Las reglas 2 y 3 son tipo *ratchet*: las violaciones preexistentes a la auditoría
del 2026-06-11 están declaradas en las constantes ``DEUDA_*`` y no fallan, pero
(a) cualquier violación NUEVA falla al instante, y (b) saldar una entrada sin
borrarla de la lista también falla — la deuda solo puede achicarse, nunca crecer.

Si la regla 1 o la 3 te fallan: el símbolo que necesitás o bien es lógica de
dominio mal ubicada (movela a core/), o bien es un detalle que la capa no debería
conocer. El patrón ya existe en el proyecto ("Settings VOs" del CLAUDE.md): el
consumidor declara su VO de settings en SU capa y ``infrastructure/container.py``
(o ``config.py``) lo construye — infrastructure puede importar adapters; al revés
jamás.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

RAIZ_REPO = Path(__file__).resolve().parents[2]
CORE_DIR = RAIZ_REPO / "core"
ADAPTERS_DIR = RAIZ_REPO / "adapters"

CAPAS_PROHIBIDAS_CORE = ("adapters", "infrastructure")

# Paquetes first-party del repo — no son "terceros" para la regla 2 (los cruces
# entre capas locales ya los cubren las reglas 1 y 3).
PAQUETES_LOCALES = frozenset({"core", "adapters", "infrastructure", "inaki", "ext", "tests"})

ALLOWLIST_TERCEROS_CORE = frozenset(
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
# web_search vive en global.secrets.yaml). Mantener vacío.
DEUDA_TERCEROS_CORE: frozenset[tuple[str, str]] = frozenset()

# Regla 3 — el grueso se salda mudando los DTOs Resolved*Config de
# infrastructure/config.py a la capa adapters (patrón Settings VOs). Los imports
# de infrastructure.container (bot.py, delegate_tool, rest, daemon) requieren
# ports/VOs propios y se saldan en el refactor de cada adapter.
DEUDA_ADAPTERS_INFRA = frozenset(
    {
        ("adapters/inbound/cli/knowledge_cli.py", "infrastructure.config"),
        ("adapters/inbound/cli/knowledge_cli.py", "infrastructure.factories.embedding_factory"),
        ("adapters/inbound/cli/scheduler_cli.py", "infrastructure.config"),
        ("adapters/inbound/daemon/runner.py", "infrastructure.config"),
        ("adapters/inbound/daemon/runner.py", "infrastructure.container"),
        ("adapters/inbound/rest/admin/app.py", "infrastructure.container"),
        ("adapters/inbound/rest/admin/routers/deps.py", "infrastructure.container"),
        ("adapters/inbound/setup_tui/screens/_base.py", "infrastructure.config"),
        ("adapters/inbound/setup_tui/screens/agent_detail_page.py", "infrastructure.config"),
        ("adapters/inbound/setup_tui/screens/global_page.py", "infrastructure.config"),
        ("adapters/inbound/telegram/bot.py", "infrastructure.config"),
        ("adapters/inbound/telegram/bot.py", "infrastructure.container"),
        ("adapters/outbound/embedding/base.py", "infrastructure.config"),
        ("adapters/outbound/embedding/e5_onnx.py", "infrastructure.config"),
        ("adapters/outbound/embedding/openai.py", "infrastructure.config"),
        ("adapters/outbound/history/sqlite_history_store.py", "infrastructure.config"),
        ("adapters/outbound/providers/base.py", "infrastructure.config"),
        ("adapters/outbound/providers/deepseek.py", "infrastructure.config"),
        ("adapters/outbound/providers/groq.py", "infrastructure.config"),
        ("adapters/outbound/providers/ollama.py", "infrastructure.config"),
        ("adapters/outbound/providers/openai.py", "infrastructure.config"),
        ("adapters/outbound/providers/openai_responses.py", "infrastructure.config"),
        ("adapters/outbound/providers/openrouter.py", "infrastructure.config"),
        ("adapters/outbound/scheduler/dispatch_adapters.py", "infrastructure.config"),
        ("adapters/outbound/tools/delegate_tool.py", "infrastructure.container"),
        ("adapters/outbound/transcription/base.py", "infrastructure.config"),
        ("adapters/outbound/transcription/groq.py", "infrastructure.config"),
    }
)


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


def test_core_no_importa_adapters_ni_infrastructure() -> None:
    assert CORE_DIR.is_dir(), f"No se encontró el directorio core/ en {CORE_DIR}"

    violaciones: list[str] = []
    for archivo in sorted(CORE_DIR.rglob("*.py")):
        for modulo in _imports_de(archivo):
            raiz = modulo.split(".")[0]
            if raiz in CAPAS_PROHIBIDAS_CORE:
                rel = archivo.relative_to(RAIZ_REPO)
                violaciones.append(f"{rel} importa '{modulo}'")

    assert not violaciones, (
        "Violación de la regla hexagonal — core/ no puede importar de "
        "adapters/ ni infrastructure/:\n  " + "\n  ".join(violaciones)
    )


def test_core_solo_usa_terceros_del_allowlist() -> None:
    """Regla 2: terceros en core/ limitados a ALLOWLIST_TERCEROS_CORE.

    Sumar una librería al allowlist es una decisión de arquitectura: el dominio
    entero queda acoplado a ella. Si de verdad hace falta, agregala con un
    comentario que justifique el porqué (como numpy) y documentala en CLAUDE.md.
    """
    assert CORE_DIR.is_dir(), f"No se encontró el directorio core/ en {CORE_DIR}"

    actuales: set[tuple[str, str]] = set()
    for archivo in sorted(CORE_DIR.rglob("*.py")):
        rel = archivo.relative_to(RAIZ_REPO).as_posix()
        for modulo in _imports_de(archivo):
            raiz = modulo.split(".")[0]
            if raiz in sys.stdlib_module_names or raiz in PAQUETES_LOCALES:
                continue
            if raiz not in ALLOWLIST_TERCEROS_CORE:
                actuales.add((rel, raiz))

    _assert_ratchet(actuales, DEUDA_TERCEROS_CORE, "core solo terceros del allowlist")


def test_adapters_no_importa_infrastructure() -> None:
    """Regla 3: adapters/ no conoce infrastructure/ (dirección: nunca al revés)."""
    assert ADAPTERS_DIR.is_dir(), f"No se encontró el directorio adapters/ en {ADAPTERS_DIR}"

    actuales: set[tuple[str, str]] = set()
    for archivo in sorted(ADAPTERS_DIR.rglob("*.py")):
        rel = archivo.relative_to(RAIZ_REPO).as_posix()
        for modulo in _imports_de(archivo):
            if modulo.split(".")[0] == "infrastructure":
                actuales.add((rel, modulo))

    _assert_ratchet(actuales, DEUDA_ADAPTERS_INFRA, "adapters no importa infrastructure")
