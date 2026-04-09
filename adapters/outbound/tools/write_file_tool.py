"""WriteFileTool — atomic write or append, optional parent directory creation."""

from __future__ import annotations

import contextlib
import json
import logging
import uuid

from adapters.outbound.tools.path_resolution import resolve_path
from core.ports.outbound.tool_port import ITool, ToolResult

logger = logging.getLogger(__name__)


class WriteFileTool(ITool):
    name = "write_file"
    description = (
        "Writes content to a file. "
        "overwrite=True truncates and replaces (destructive mode, atomic temp+rename). "
        "overwrite=False (default, safe mode) appends with a leading newline if the file exists "
        "and is non-empty, or creates the file if missing. "
        "Returns JSON: success, resolved path, lines_written."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "Destination path (absolute or relative to CWD; ~ expanded). "
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
            "overwrite": {
                "type": "boolean",
                "description": (
                    "If true, truncate and replace entire file content (destructive). "
                    "If false (default), append with leading newline or create if missing (safe)."
                ),
                "default": False,
            },
        },
        "required": ["file_path", "content"],
    }

    async def execute(
        self,
        file_path: str,
        content: str,
        create_dirs: bool | None = False,
        overwrite: bool | None = False,
        **kwargs,
    ) -> ToolResult:
        if create_dirs is None:
            create_dirs = False
        if overwrite is None:
            overwrite = False
        resolved = resolve_path(file_path)

        parent = resolved.parent
        if not parent.exists():
            if create_dirs:
                try:
                    parent.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    logger.error("write_file impossible de créer le parent %s: %s", parent, exc)
                    payload = {"success": False, "error": f"Cannot create parent directory: {exc}"}
                    return ToolResult(
                        tool_name=self.name,
                        output=json.dumps(payload, ensure_ascii=False),
                        success=False,
                        error=str(exc),
                    )
            else:
                logger.warning("write_file répertoire parent absent: %s", parent)
                payload = {
                    "success": False,
                    "error": (
                        "Parent directory does not exist. Set create_dirs=true to create it."
                    ),
                }
                return ToolResult(
                    tool_name=self.name,
                    output=json.dumps(payload, ensure_ascii=False),
                    success=False,
                    error=payload["error"],
                )

        if overwrite:
            temp_path = resolved.with_suffix(f".tmp.{uuid.uuid4().hex}{resolved.suffix}")
            try:
                temp_path.write_text(content, encoding="utf-8")
                temp_path.rename(resolved)
            except OSError as exc:
                logger.error("write_file écriture atomique échouée pour %s: %s", resolved, exc)
                if temp_path.exists():
                    with contextlib.suppress(OSError):
                        temp_path.unlink()
                payload = {"success": False, "error": f"Write failed: {exc}"}
                return ToolResult(
                    tool_name=self.name,
                    output=json.dumps(payload, ensure_ascii=False),
                    success=False,
                    error=str(exc),
                )
        else:
            file_exists_and_nonempty = resolved.exists() and resolved.stat().st_size > 0
            try:
                with open(resolved, "a", encoding="utf-8") as f:
                    if file_exists_and_nonempty:
                        f.write(f"\n{content}")
                    else:
                        f.write(content)
            except OSError as exc:
                logger.error("write_file append échoué pour %s: %s", resolved, exc)
                payload = {"success": False, "error": f"Write failed: {exc}"}
                return ToolResult(
                    tool_name=self.name,
                    output=json.dumps(payload, ensure_ascii=False),
                    success=False,
                    error=str(exc),
                )

        lines_written = len(content.splitlines())
        logger.info("write_file %d lignes écrites vers %s", lines_written, resolved)
        payload = {
            "success": True,
            "path": str(resolved),
            "lines_written": lines_written,
        }
        return ToolResult(
            tool_name=self.name,
            output=json.dumps(payload, ensure_ascii=False),
            success=True,
        )
