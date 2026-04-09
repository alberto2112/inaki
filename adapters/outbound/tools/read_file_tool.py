"""ReadFileTool — read file content with pagination (offset, max_lines)."""

from __future__ import annotations

import json
import logging

from adapters.outbound.tools.path_resolution import resolve_path
from core.ports.outbound.tool_port import ITool, ToolResult

logger = logging.getLogger(__name__)


class ReadFileTool(ITool):
    name = "read_file"
    description = (
        "Reads a file with optional pagination (offset + max_lines). "
        "Returns JSON with content, line_count, and truncated. "
        "Accepts absolute paths or paths relative to the process current working directory."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "Path to the file (absolute or relative to Iñaki's current working directory). "
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
        },
        "required": ["file_path"],
    }

    async def execute(
        self,
        file_path: str,
        max_lines: int | None = 0,
        offset: int | None = 0,
        **kwargs,
    ) -> ToolResult:
        if max_lines is None:
            max_lines = 0
        if offset is None:
            offset = 0
        resolved = resolve_path(file_path)

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

        truncated = False
        if offset > 0 or max_lines > 0:
            truncated = (offset > 0) or (max_lines > 0 and max_lines < total_lines)
            lines = lines[offset : offset + max_lines if max_lines > 0 else None]
            content = "\n".join(lines)
        else:
            content = text

        # Same shape as legacy project: no success key on the nominal success path
        payload = {
            "content": content,
            "line_count": total_lines,
            "truncated": truncated,
        }
        return ToolResult(
            tool_name=self.name,
            output=json.dumps(payload, ensure_ascii=False),
            success=True,
        )
