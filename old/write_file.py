"""
Tool : écriture atomique de fichiers avec création optionnelle de répertoires parents.

Écrit du contenu dans un fichier de façon atomique (temp + rename) ou en append.
"""

import contextlib
import logging
import uuid
from typing import Any

from tools.shell_paths import resolve_path

logger = logging.getLogger(__name__)

TOOL_ENABLED = True
TOOL_NAME = "write_file"
TOOL_VERSION = "1.0.0"
TOOL_DESCRIPTION = (
    "Writes content to a file. "
    "Use overwrite=True to truncate and replace file content (dangerous, atomic via temp+rename). "
    "Use overwrite=False (default, safe mode) to append with newline prefix "
    "to an existing file, or create if nonexistent. "
    "Perfect for taking notes, saving content, and editing files. "
    "Returns structured JSON with success status, resolved path, and lines written."
)
TOOL_PARAMETERS = [
    {
        "name": "file_path",
        "type": "string",
        "description": (
            "Destination path (absolute or relative to process CWD; ~ expanded). "
            "Use 'file_path', not 'path'. "
            "Parent directory must exist unless create_dirs=true."
        ),
        "required": True,
    },
    {
        "name": "content",
        "type": "string",
        "description": "Content to write to the file.",
        "required": True,
    },
    {
        "name": "create_dirs",
        "type": "boolean",
        "description": ("If true, create parent directories if they do not exist. Default: false."),
        "required": False,
    },
    {
        "name": "overwrite",
        "type": "boolean",
        "description": (
            "If true, truncate file and write new content (dangerous mode). "
            "If false (default), append with newline prefix or create file if missing (safe mode)."
        ),
        "required": False,
        "default": False,
    },
]


async def run(file_path: str, content: str, create_dirs: bool = False, overwrite: bool = False) -> Any:
    """
    Écrit du contenu dans un fichier de façon atomique ou en mode append.

    Args:
        file_path: Chemin du fichier à écrire.
        content: Contenu texte à écrire.
        create_dirs: Créer les répertoires parents si manquants.
        overwrite: If True (dangerous), truncate and replace content using atomic temp+rename.
                  If False (safe, default), append content with newline prefix to existing file,
                  or create file if it doesn't exist.

    Returns:
        Dict avec ``success``, ``path``, ``lines_written``.
        En cas d'erreur : ``{"success": false, "error": ...}``.
    """
    resolved = resolve_path(file_path)

    # Vérifier ou créer le répertoire parent
    parent = resolved.parent
    if not parent.exists():
        if create_dirs:
            try:
                parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.error("write_file cannot create parent dir %s: %s", parent, exc)
                return {"success": False, "error": f"Cannot create parent directory: {exc}"}
        else:
            logger.warning("write_file missing parent dir: %s", parent)
            return {
                "success": False,
                "error": "Parent directory does not exist. Set create_dirs=true to create it.",
            }

    # Écriture selon le mode
    if overwrite:
        # Dangerous mode: atomic truncate via temp file + rename
        temp_path = resolved.with_suffix(f".tmp.{uuid.uuid4().hex}{resolved.suffix}")
        try:
            temp_path.write_text(content, encoding="utf-8")
            temp_path.rename(resolved)
        except OSError as exc:
            logger.error("write_file atomic write failed for %s: %s", resolved, exc)
            if temp_path.exists():
                with contextlib.suppress(OSError):
                    temp_path.unlink()
            return {"success": False, "error": f"Write failed: {exc}"}
    else:
        # Safe mode: append with newline prefix (only if file exists and is non-empty)
        file_exists_and_nonempty = resolved.exists() and resolved.stat().st_size > 0
        try:
            with open(resolved, "a", encoding="utf-8") as f:
                if file_exists_and_nonempty:
                    f.write(f"\n{content}")
                else:
                    f.write(content)
        except OSError as exc:
            logger.error("write_file append failed for %s: %s", resolved, exc)
            return {"success": False, "error": f"Write failed: {exc}"}

    lines_written = len(content.splitlines())
    logger.info("write_file wrote %d lines to %s", lines_written, resolved)
    return {
        "success": True,
        "path": str(resolved),
        "lines_written": lines_written,
    }
