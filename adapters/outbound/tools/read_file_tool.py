"""ReadFileTool — read file content with pagination (offset, max_lines)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from adapters.outbound.tools.path_resolution import (
    ContainmentMode,
    WorkspaceEscapeError,
    resolve_path,
)
from core.ports.outbound.tool_port import ITool, ToolResult

logger = logging.getLogger(__name__)


class ReadFileTool(ITool):
    name = "read_file"
    description = (
        "Reads a file with optional pagination (offset + max_lines). "
        "Returns JSON with content, line_count, and truncated. "
        "Set line_numbers=true to get every line prefixed with its 1-based number — "
        "do this whenever you intend to modify the file afterwards with patch_file, "
        "which needs exact line numbers. "
        "Paths are resolved against the agent's workspace root."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "Path to the file (absolute or relative to the agent's workspace). "
                    "~ is expanded to the home directory."
                ),
            },
            "max_lines": {
                "type": "integer",
                "description": "Maximum number of lines to return. 0 = no limit (read entire file). Default: 0.",
                "default": 0,
            },
            "offset": {
                "type": "integer",
                "description": "Number of lines to skip before reading. 0 = start from beginning. Default: 0.",
                "default": 0,
            },
            "line_numbers": {
                "type": "boolean",
                "description": (
                    "If true, each returned line is prefixed with its 1-based number in the "
                    "file followed by a tab ('  42\\ttext'). Numbers are absolute — they account "
                    "for 'offset', so they can be passed straight to patch_file. "
                    "Set this to true before patching; leave false (default) when you only "
                    "need the raw content."
                ),
                "default": False,
            },
        },
        "required": ["file_path"],
    }

    def __init__(self, workspace: Path, containment: ContainmentMode = "strict") -> None:
        self._workspace = workspace
        self._containment = containment

    async def execute(  # type: ignore[override]
        self,
        file_path: str,
        max_lines: int | None = 0,
        offset: int | None = 0,
        line_numbers: bool | None = False,
        **kwargs,
    ) -> ToolResult:
        if max_lines is None:
            max_lines = 0
        if offset is None:
            offset = 0
        if line_numbers is None:
            line_numbers = False
        try:
            resolved = resolve_path(file_path, self._workspace, self._containment)
        except WorkspaceEscapeError as exc:
            logger.warning("read_file containment violation: %s", exc)
            payload = {"success": False, "error": str(exc)}
            return ToolResult(
                tool_name=self.name,
                output=json.dumps(payload, ensure_ascii=False),
                success=False,
                error=str(exc),
            )

        try:
            text = resolved.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("read_file fichier absent: %s", resolved)
            payload = {"success": False, "error": "File not found"}
            return ToolResult(
                tool_name=self.name,
                output=json.dumps(payload, ensure_ascii=False),
                success=False,
                error="File not found",
            )
        except OSError as exc:
            logger.error("read_file erreur OS %s: %s", resolved, exc)
            payload = {"success": False, "error": f"Cannot read file: {exc}"}
            return ToolResult(
                tool_name=self.name,
                output=json.dumps(payload, ensure_ascii=False),
                success=False,
                error=str(exc),
            )

        lines = text.splitlines()
        total_lines = len(lines)

        paginated = offset > 0 or max_lines > 0
        truncated = False
        if paginated:
            truncated = (offset > 0) or (max_lines > 0 and max_lines < total_lines)
            visible = lines[offset : offset + max_lines if max_lines > 0 else None]
        else:
            visible = lines

        if line_numbers:
            # Numeración ABSOLUTA (arranca en offset+1): los números se pasan tal cual a
            # patch_file, sin que el LLM tenga que recalcular nada por la paginación.
            last_number = offset + len(visible)
            width = len(str(last_number)) if last_number > 0 else 1
            content = "\n".join(
                f"{offset + i + 1:>{width}}\t{line}" for i, line in enumerate(visible)
            )
        elif paginated:
            content = "\n".join(visible)
        else:
            content = text

        # Same shape as legacy project: no success key on the nominal success path
        payload = {
            "content": content,
            "line_count": total_lines,
            "truncated": truncated,
            "line_numbers": line_numbers,
        }
        return ToolResult(
            tool_name=self.name,
            output=json.dumps(payload, ensure_ascii=False),
            success=True,
        )
