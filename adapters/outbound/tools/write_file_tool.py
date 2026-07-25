"""WriteFileTool — atomic write or append, optional parent directory creation."""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from pathlib import Path

from adapters.outbound.tools.path_resolution import (
    ContainmentMode,
    WorkspaceEscapeError,
    resolve_path,
)
from core.ports.outbound.tool_port import ITool, ToolResult

logger = logging.getLogger(__name__)

_VALID_MODES = ("create", "overwrite", "append")

# Mensajes de error: son el canal por el que el LLM APRENDE la decisión correcta.
# Cada uno nombra la alternativa concreta, no solo el fallo.
_MODE_HINT = (
    "To modify part of the file, do not use write_file at all: use edit_file (match by "
    "pattern) or patch_file (exact line ranges, after read_file with line_numbers=true). "
    "Use write_file with mode='overwrite' only when 'content' is the complete new file, "
    "or mode='append' to add at the end while keeping what is there."
)
_MISSING_MODE_MSG = (
    "This file already exists and is not empty, so 'mode' must be stated explicitly. " + _MODE_HINT
)
_LEGACY_OVERWRITE_MSG = (
    "The 'overwrite' parameter no longer exists; use 'mode' instead "
    "('create' | 'overwrite' | 'append'). " + _MODE_HINT
)


class WriteFileTool(ITool):
    name = "write_file"
    description = (
        "Writes a WHOLE file: creates a new one, replaces one entirely, or appends to the end. "
        "This tool CANNOT modify existing content in place. "
        "To change part of a file that already exists, do NOT use write_file — use edit_file "
        "(match by pattern, no line numbers needed) or patch_file (exact line ranges). "
        "Sending an updated copy of a file through write_file is the usual cause of duplicated "
        "or destroyed content. "
        "When the target already exists and is not empty, 'mode' is mandatory: there is no "
        "default, so the choice between replacing and appending is always explicit. "
        "Returns JSON: success, resolved path, mode, lines_written."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "Destination path (absolute or relative to the agent's workspace; ~ expanded). "
                    "Use 'file_path', not 'path'. "
                    "Parent directory must exist unless create_dirs=true."
                ),
            },
            "content": {
                "type": "string",
                "description": "Content to write to the file.",
            },
            "create_dirs": {
                "type": "boolean",
                "description": "If true, create missing parent directories. Default: false.",
                "default": False,
            },
            "mode": {
                "type": "string",
                "enum": ["create", "overwrite", "append"],
                "description": (
                    "'create': write a brand-new file; fails if it already exists with content. "
                    "'overwrite': replace the ENTIRE file content (everything currently there is "
                    "lost; only correct when 'content' is the complete new file). "
                    "'append': add 'content' at the end, keeping what is already there (logs, "
                    "journals, notes). "
                    "No default: may be omitted only when the target does not exist yet or is "
                    "empty (treated as 'create'). Omitting it on an existing non-empty file is an "
                    "error, not a silent append."
                ),
            },
        },
        "required": ["file_path", "content"],
    }

    def __init__(self, workspace: Path, containment: ContainmentMode = "strict") -> None:
        self._workspace = workspace
        self._containment = containment

    async def execute(  # type: ignore[override]
        self,
        file_path: str,
        content: str,
        create_dirs: bool | None = False,
        mode: str | None = None,
        overwrite: bool | None = None,
        **kwargs,
    ) -> ToolResult:
        if create_dirs is None:
            create_dirs = False

        # Corte limpio: el bool `overwrite` desapareció. Si llega, NO lo interpretamos —
        # un mapeo silencioso a un modo es exactamente el fallo que este cambio elimina.
        if overwrite is not None:
            return self._error(_LEGACY_OVERWRITE_MSG)

        if mode is not None and mode not in _VALID_MODES:
            return self._error(
                f"Invalid mode {mode!r}; expected one of {list(_VALID_MODES)}. {_MODE_HINT}"
            )

        try:
            resolved = resolve_path(file_path, self._workspace, self._containment)
        except WorkspaceEscapeError as exc:
            logger.warning("write_file containment violation: %s", exc)
            return self._error(str(exc))

        parent = resolved.parent
        if not parent.exists():
            if create_dirs:
                try:
                    parent.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    logger.error("write_file impossible de créer le parent %s: %s", parent, exc)
                    return self._error(f"Cannot create parent directory: {exc}")
            else:
                logger.warning("write_file répertoire parent absent: %s", parent)
                return self._error(
                    "Parent directory does not exist. Set create_dirs=true to create it."
                )

        target_has_content = resolved.exists() and resolved.stat().st_size > 0

        # El corazón del cambio: sobre un fichero con contenido, el modo NO se asume.
        # El default histórico (append silencioso) convertía "acá tenés el fichero
        # actualizado" en el fichero viejo + el nuevo pegado al final.
        if mode is None:
            if target_has_content:
                logger.warning("write_file modo ausente sobre fichero no vacío: %s", resolved)
                return self._error(_MISSING_MODE_MSG)
            mode = "create"

        if mode == "create" and target_has_content:
            logger.warning("write_file mode=create sobre fichero no vacío: %s", resolved)
            return self._error(
                "File already exists and is not empty, so mode='create' cannot be used. "
                f"{_MODE_HINT}"
            )

        if mode in ("create", "overwrite"):
            temp_path = resolved.with_suffix(f".tmp.{uuid.uuid4().hex}{resolved.suffix}")
            try:
                temp_path.write_text(content, encoding="utf-8")
                temp_path.rename(resolved)
            except OSError as exc:
                logger.error("write_file écriture atomique échouée pour %s: %s", resolved, exc)
                if temp_path.exists():
                    with contextlib.suppress(OSError):
                        temp_path.unlink()
                return self._error(f"Write failed: {exc}")
        else:  # append
            try:
                with open(resolved, "a", encoding="utf-8") as f:
                    if target_has_content:
                        f.write(f"\n{content}")
                    else:
                        f.write(content)
            except OSError as exc:
                logger.error("write_file append échoué pour %s: %s", resolved, exc)
                return self._error(f"Write failed: {exc}")

        lines_written = len(content.splitlines())
        logger.info("write_file %d lignes écrites vers %s (mode=%s)", lines_written, resolved, mode)
        payload = {
            "success": True,
            "path": str(resolved),
            "mode": mode,
            "lines_written": lines_written,
        }
        return ToolResult(
            tool_name=self.name,
            output=json.dumps(payload, ensure_ascii=False),
            success=True,
        )

    def _error(self, message: str) -> ToolResult:
        payload = {"success": False, "error": message}
        return ToolResult(
            tool_name=self.name,
            output=json.dumps(payload, ensure_ascii=False),
            success=False,
            error=message,
        )
