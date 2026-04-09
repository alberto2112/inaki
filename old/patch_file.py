"""
Tool : patch a file by applying line-range patches (insert/replace/delete).

Patches are applied in descending line order internally to prevent line drift.
Atomic write (temp + rename).
"""

import contextlib
import logging
import uuid
from typing import Any

from tools.shell_paths import resolve_path

logger = logging.getLogger(__name__)

TOOL_ENABLED = True
TOOL_NAME = "patch_file"
TOOL_VERSION = "1.0.0"
TOOL_DESCRIPTION = (
    "Applies line-range patches to a file. "
    "Line numbers are 1-indexed (first line = 1). "
    "Patches with null content delete lines; patches with string content replace/insert at the range. "
    "Returns structured JSON with success status, resolved path, and number of patches applied."
)
TOOL_PARAMETERS = [
    {
        "name": "file_path",
        "type": "string",
        "description": (
            "Target file path (absolute or relative to process CWD; ~ expanded). "
            "Use 'file_path', not 'path'. Parent directory must already exist."
        ),
        "required": True,
    },
    {
        "name": "patches",
        "type": "array",
        "description": "Array of patch objects to apply in descending line order internally.",
        "required": True,
        "items_schema": {
            "type": "object",
            "properties": {
                "start_line": {
                    "type": "integer",
                    "description": "First line number to patch (1-indexed, inclusive).",
                },
                "end_line": {
                    "type": "integer",
                    "description": "Last line number to patch (1-indexed, inclusive).",
                },
                "content": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"},
                    ],
                    "description": "New content to write at the range, or null to delete lines.",
                },
            },
            "required": ["start_line", "end_line"],
        },
    },
]


async def run(patches: list[dict[str, Any]], file_path: str) -> Any:
    """
    Applique des patches line-range à un fichier.

    Args:
        patches: Liste de dicts avec start_line, end_line, content (null = delete).
        file_path: Chemin du fichier à modifier.

    Returns:
        Dict avec ``success``, ``path``, ``patches_applied``.
        En cas d'erreur : ``{"success": false, "error": ...}``.
    """
    resolved = resolve_path(file_path)

    # Lire le fichier
    if not resolved.exists():
        return {"success": False, "error": f"File not found: {file_path}"}

    try:
        original_content = resolved.read_text(encoding="utf-8")
        has_trailing_newline = original_content.endswith("\n")
        lines = original_content.splitlines()
    except OSError as exc:
        logger.error("patch_file cannot read %s: %s", resolved, exc)
        return {"success": False, "error": f"Cannot read file: {exc}"}

    file_line_count = len(lines)

    # Valider tous les patches en fail-fast (avant toute modification)
    for i, patch in enumerate(patches):
        start = patch.get("start_line")
        end = patch.get("end_line")

        if not isinstance(start, int) or not isinstance(end, int):
            return {
                "success": False,
                "error": f"Patch {i}: start_line and end_line must be integers",
            }

        if start <= 0:
            return {
                "success": False,
                "error": f"Patch {i}: start_line must be greater than 0, got {start}",
            }

        if start > end:
            return {
                "success": False,
                "error": f"Patch {i}: start_line must not exceed end_line (got start_line={start}, end_line={end})",
            }

        if end > file_line_count:
            return {
                "success": False,
                "error": f"Patch {i}: end_line {end} exceeds file length {file_line_count}",
            }

    # Trier les patches en ordre descendant pour éviter le drift de lignes
    sorted_patches = sorted(patches, key=lambda p: p["start_line"], reverse=True)

    # Appliquer chaque patch
    for patch in sorted_patches:
        start = patch["start_line"]  # 1-indexed
        end = patch["end_line"]  # 1-indexed, inclusive
        content = patch.get("content")

        # Convertir en index 0-based: start-1 .. end-1
        idx_start = start - 1
        idx_end = end  # slice end est exclusive

        if content is None:
            # Supprimer les lignes
            del lines[idx_start:idx_end]
        else:
            # Remplacer/insérer les lignes
            lines[idx_start:idx_end] = content.splitlines(keepends=False)

    # Écriture atomique : temp file + rename
    temp_path = resolved.with_suffix(f".tmp.{uuid.uuid4().hex}{resolved.suffix}")
    try:
        final_content = "\n".join(lines)
        if has_trailing_newline:
            final_content += "\n"
        temp_path.write_text(final_content, encoding="utf-8")
        temp_path.rename(resolved)
    except OSError as exc:
        logger.error("patch_file atomic write failed for %s: %s", resolved, exc)
        if temp_path.exists():
            with contextlib.suppress(OSError):
                temp_path.unlink()
        return {"success": False, "error": f"Write failed: {exc}"}

    logger.info("patch_file applied %d patches to %s", len(patches), resolved)
    return {
        "success": True,
        "path": str(resolved),
        "patches_applied": len(patches),
    }
