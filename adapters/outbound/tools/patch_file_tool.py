"""PatchFileTool — line-range patches (insert/replace/delete)."""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from adapters.outbound.tools.path_resolution import (
    ContainmentMode,
    WorkspaceEscapeError,
    resolve_path,
)
from core.ports.outbound.tool_port import ITool, ToolResult

logger = logging.getLogger(__name__)


class PatchFileTool(ITool):
    name = "patch_file"
    description = (
        "Applies line-range patches to a file. "
        "Line numbers are 1-based (first line = 1). "
        "A patch with null content deletes lines; a string replaces/inserts over the range. "
        "Returns structured JSON: success, path (resolved), patches_applied, or error."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "Target file path (absolute or relative to the agent's workspace; ~ expanded). "
                    "Use 'file_path', not 'path'. Parent directory must already exist."
                ),
            },
            "patches": {
                "type": "array",
                "description": (
                    "List of patches; applied internally in descending start_line order "
                    "to avoid line-number drift."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "start_line": {
                            "type": "integer",
                            "description": "First line to patch (1-based, inclusive).",
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "Last line to patch (1-based, inclusive).",
                        },
                        "content": {
                            "anyOf": [
                                {"type": "string"},
                                {"type": "null"},
                            ],
                            "description": "New content for the range, or null to delete those lines.",
                        },
                    },
                    "required": ["start_line", "end_line"],
                },
            },
        },
        "required": ["file_path", "patches"],
    }

    def __init__(self, workspace: Path, containment: ContainmentMode = "strict") -> None:
        self._workspace = workspace
        self._containment = containment

    async def execute(self, patches: list[dict[str, Any]], file_path: str, **kwargs) -> ToolResult:
        try:
            resolved = resolve_path(file_path, self._workspace, self._containment)
        except WorkspaceEscapeError as exc:
            logger.warning("patch_file containment violation: %s", exc)
            payload = {"success": False, "error": str(exc)}
            return ToolResult(
                tool_name=self.name,
                output=json.dumps(payload, ensure_ascii=False),
                success=False,
                error=str(exc),
            )

        if not resolved.exists():
            payload = {"success": False, "error": f"File not found: {file_path}"}
            return ToolResult(
                tool_name=self.name,
                output=json.dumps(payload, ensure_ascii=False),
                success=False,
                error=payload["error"],
            )

        try:
            original_content = resolved.read_text(encoding="utf-8")
            has_trailing_newline = original_content.endswith("\n")
            lines = original_content.splitlines()
        except OSError as exc:
            logger.error("patch_file lecture impossible %s: %s", resolved, exc)
            payload = {"success": False, "error": f"Cannot read file: {exc}"}
            return ToolResult(
                tool_name=self.name,
                output=json.dumps(payload, ensure_ascii=False),
                success=False,
                error=str(exc),
            )

        file_line_count = len(lines)

        for i, patch in enumerate(patches):
            start = patch.get("start_line")
            end = patch.get("end_line")

            if not isinstance(start, int) or not isinstance(end, int):
                payload = {
                    "success": False,
                    "error": f"Patch {i}: start_line and end_line must be integers",
                }
                return ToolResult(
                    tool_name=self.name,
                    output=json.dumps(payload, ensure_ascii=False),
                    success=False,
                    error=payload["error"],
                )

            if start <= 0:
                payload = {
                    "success": False,
                    "error": f"Patch {i}: start_line must be greater than 0, got {start}",
                }
                return ToolResult(
                    tool_name=self.name,
                    output=json.dumps(payload, ensure_ascii=False),
                    success=False,
                    error=payload["error"],
                )

            if start > end:
                payload = {
                    "success": False,
                    "error": (
                        f"Patch {i}: start_line must not exceed end_line "
                        f"(got start_line={start}, end_line={end})"
                    ),
                }
                return ToolResult(
                    tool_name=self.name,
                    output=json.dumps(payload, ensure_ascii=False),
                    success=False,
                    error=payload["error"],
                )

            if end > file_line_count:
                payload = {
                    "success": False,
                    "error": f"Patch {i}: end_line {end} exceeds file length {file_line_count}",
                }
                return ToolResult(
                    tool_name=self.name,
                    output=json.dumps(payload, ensure_ascii=False),
                    success=False,
                    error=payload["error"],
                )

        sorted_patches = sorted(patches, key=lambda p: p["start_line"], reverse=True)

        for patch in sorted_patches:
            start = patch["start_line"]
            end = patch["end_line"]
            content = patch.get("content")

            idx_start = start - 1
            idx_end = end

            if content is None:
                del lines[idx_start:idx_end]
            else:
                lines[idx_start:idx_end] = content.splitlines(keepends=False)

        temp_path = resolved.with_suffix(f".tmp.{uuid.uuid4().hex}{resolved.suffix}")
        try:
            final_content = "\n".join(lines)
            if has_trailing_newline:
                final_content += "\n"
            temp_path.write_text(final_content, encoding="utf-8")
            temp_path.rename(resolved)
        except OSError as exc:
            logger.error("patch_file écriture atomique échouée pour %s: %s", resolved, exc)
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

        logger.info("patch_file %d patches appliqués sur %s", len(patches), resolved)
        payload = {
            "success": True,
            "path": str(resolved),
            "patches_applied": len(patches),
        }
        return ToolResult(
            tool_name=self.name,
            output=json.dumps(payload, ensure_ascii=False),
            success=True,
        )
