"""Resolución del modo debug: flag de proceso (``--debug``) sobre config (``app.debug``).

Mismo patrón que ``inaki/config/home.py``: un override de proceso que el
bootstrap fija UNA vez, antes de cargar config, y que gana sobre el YAML.
``None`` limpia el override (pensado para aislar tests).
"""

from __future__ import annotations

_override: bool | None = None


def set_debug_override(value: bool | None) -> None:
    """Fija (o limpia con ``None``) el override de proceso del modo debug."""
    global _override
    _override = value


def is_debug_enabled(configured: bool) -> bool:
    """``--debug`` gana sobre ``app.debug``; sin flag, manda la config."""
    return configured if _override is None else _override
