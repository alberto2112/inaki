"""
Tool : lecture structurée de fichiers avec pagination.

Lit le contenu d'un fichier avec offset et limite de lignes optionnels.
"""

import logging
from typing import Any

from tools.shell_paths import resolve_path

logger = logging.getLogger(__name__)

TOOL_ENABLED = True
TOOL_NAME = "read_file"
TOOL_VERSION = "1.0.0"
TOOL_DESCRIPTION = (
    "Reads a file's content with optional pagination (offset + max_lines). "
    "Returns structured JSON with content, line_count, and truncated flag. "
    "Accepts absolute paths or paths relative to the process current directory."
)
TOOL_PARAMETERS = [
    {
        "name": "file_path",
        "type": "string",
        "description": (
            "Path to the file (absolute, or relative to Inaki's current working directory). "
            "~ is expanded to the home directory."
        ),
        "required": True,
    },
    {
        "name": "max_lines",
        "type": "integer",
        "description": ("Maximum number of lines to return. 0 = no limit (read all). Default: 0."),
        "required": False,
    },
    {
        "name": "offset",
        "type": "integer",
        "description": ("Number of lines to skip before reading. 0 = start from beginning. Default: 0."),
        "required": False,
    },
]


async def run(file_path: str, max_lines: int = 0, offset: int = 0) -> Any:
    """
    Lit un fichier avec pagination optionnelle.

    Args:
        file_path: Chemin du fichier à lire.
        max_lines: Nombre max de lignes à retourner (0 = tout).
        offset: Nombre de lignes à ignorer au début (0 = depuis le début).

    Returns:
        Dict avec ``content``, ``line_count``, ``truncated``.
        En cas d'erreur : ``{"success": false, "error": ...}``.
    """
    resolved = resolve_path(file_path)

    try:
        text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("read_file file not found: %s", resolved)
        return {"success": False, "error": "File not found"}
    except OSError as exc:
        logger.error("read_file OS error %s: %s", resolved, exc)
        return {"success": False, "error": f"Cannot read file: {exc}"}

    lines = text.splitlines()
    total_lines = len(lines)

    truncated = False
    if offset > 0 or max_lines > 0:
        truncated = (offset > 0) or (max_lines > 0 and max_lines < total_lines)
        lines = lines[offset : offset + max_lines if max_lines > 0 else None]
        content = "\n".join(lines)
    else:
        content = text

    return {
        "content": content,
        "line_count": total_lines,
        "truncated": truncated,
    }
