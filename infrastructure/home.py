"""
Home de la instancia Inaki — la raíz ÚNICA que ancla config, data, ``secret.key``,
``tool_config``, ``users`` y knowledge para UN proceso.

Invariante: **1 proceso = 1 home** (ver "Tiers de recursos" en ``CLAUDE.md``). El
home se resuelve en este orden de precedencia:

  1. Override explícito vía :func:`set_inaki_home` — lo setea el flag ``--home``.
  2. Variable de entorno ``INAKI_HOME``.
  3. Default ``~/.inaki`` (backward-compat — el deploy de siempre, sin migración).

Este módulo vive en ``infrastructure/`` a propósito: ``core/`` y ``adapters/`` NO lo
importan (ratchet hexagonal de ``test_architecture.py``). Esas capas reciben los paths
ya resueltos por inyección (Settings VO) o por campos ``RuntimePath`` de config.

Secuencia obligatoria: :func:`set_inaki_home` debe correr en el bootstrap ANTES de
cargar cualquier config, porque el validador ``RuntimePath`` ancla contra
:func:`get_inaki_home` en tiempo de validación.
"""

from __future__ import annotations

import os
from pathlib import Path

# Default computado al importar (el home del SO no cambia durante un proceso).
_DEFAULT_HOME = Path.home() / ".inaki"

# Override de proceso seteado por el bootstrap (flag --home). ``None`` = sin override.
_override: Path | None = None


def set_inaki_home(path: Path | str | None) -> None:
    """Fija el home de la instancia para ESTE proceso.

    Llamar UNA sola vez en el bootstrap, ANTES de cargar config. Pasar ``None``
    limpia el override (vuelve a env/default) — pensado para aislar tests.
    """
    global _override
    _override = None if path is None else Path(path).expanduser()


def get_inaki_home() -> Path:
    """Devuelve la raíz del home de la instancia.

    Resuelve en orden: override explícito → env ``INAKI_HOME`` → default ``~/.inaki``.
    """
    if _override is not None:
        return _override
    env = os.environ.get("INAKI_HOME")
    if env:
        return Path(env).expanduser()
    return _DEFAULT_HOME
