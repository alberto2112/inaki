"""Resolución de paths de filesystem para las tools, con guard de contención al workspace."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

ContainmentMode = Literal["strict", "warn", "off"]


class WorkspaceEscapeError(Exception):
    """Raised when a resolved path escapes the workspace root under strict containment."""


def resolve_path(
    file_path: str,
    workspace: Path,
    containment: ContainmentMode = "strict",
) -> Path:
    """
    Resuelve un path del LLM contra el workspace del agente.

    - Expande `~` al home del usuario.
    - Paths absolutos se resuelven tal cual (canonical form).
    - Paths relativos se resuelven contra `workspace`, NO contra el cwd del proceso.

    Containment guard (aplicado a paths absolutos y a escapes via `..`):
      - "strict" → levanta WorkspaceEscapeError si el path sale del workspace.
      - "warn"   → loggea warning y permite el acceso.
      - "off"    → sin check.
    """
    expanded = Path(file_path).expanduser()
    if expanded.is_absolute():
        resolved = expanded.resolve()
    else:
        resolved = (workspace / expanded).resolve()

    if containment == "off":
        return resolved

    workspace_root = workspace.resolve()
    try:
        resolved.relative_to(workspace_root)
    except ValueError:
        if containment == "strict":
            raise WorkspaceEscapeError(f"Path '{resolved}' escapa del workspace '{workspace_root}'")
        logger.warning(
            "Path '%s' fuera del workspace '%s' (containment=warn, permitido)",
            resolved,
            workspace_root,
        )

    return resolved
