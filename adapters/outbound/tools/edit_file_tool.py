"""EditFileTool — ediciones por patrón, línea a línea (estilo sed pero más amigable)."""

from __future__ import annotations

import contextlib
import json
import logging
import re
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

_VALID_OPS = ("replace", "insert_before", "insert_after", "delete_lines")


class EditFileTool(ITool):
    name = "edit_file"
    description = (
        "Edits a file by pattern matching (line-oriented, sed-like). "
        "Use this for quick search-and-replace, conditional inserts, or deleting matching lines "
        "without counting line numbers. For exact line-range surgery, use patch_file instead. "
        "Operations: 'replace' (substitute pattern with replacement inside matching lines), "
        "'insert_before' / 'insert_after' (add content adjacent to matching lines), "
        "'delete_lines' (remove matching lines). "
        "Matching is line-by-line — patterns cannot cross newlines. "
        "count=1 (default) applies to the first match only; count=0 applies to all matches; "
        "count=N applies to the first N matches. "
        "Atomic: if any edit's pattern has zero matches, the whole batch is rejected and the "
        "file is left untouched. "
        "Returns JSON: success, path (resolved), or error."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "Target file path (absolute or relative to the agent's workspace; ~ expanded). "
                    "Use 'file_path', not 'path'."
                ),
            },
            "edits": {
                "type": "array",
                "description": (
                    "List of edits to apply atomically and in order. If any edit's pattern has "
                    "zero matches the whole batch aborts and the file is not modified."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": list(_VALID_OPS),
                            "description": (
                                "'replace': substitute pattern with replacement inside each "
                                "matching line. "
                                "'insert_before' / 'insert_after': insert content adjacent to "
                                "matching lines. "
                                "'delete_lines': remove matching lines entirely."
                            ),
                        },
                        "pattern": {
                            "type": "string",
                            "description": (
                                "Pattern to match against each line. Literal substring by default; "
                                "Python regex when is_regex=true. Matching is per-line — patterns "
                                "cannot cross newlines."
                            ),
                        },
                        "replacement": {
                            "type": "string",
                            "description": (
                                "For op='replace': text that replaces the matched pattern. "
                                "Only the first occurrence within each matching line is replaced. "
                                "With is_regex=true, backreferences like \\1 are supported."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": (
                                "For op='insert_before' / 'insert_after': text to insert. "
                                "May contain newlines (becomes multiple inserted lines)."
                            ),
                        },
                        "is_regex": {
                            "type": "boolean",
                            "description": (
                                "If true, 'pattern' is a Python regex. Default: false (literal "
                                "substring)."
                            ),
                            "default": False,
                        },
                        "count": {
                            "type": "integer",
                            "description": (
                                "Max matches to apply. 1 (default) = first match only; "
                                "0 = all matches; N = first N matches."
                            ),
                            "default": 1,
                        },
                    },
                    "required": ["op", "pattern"],
                },
            },
        },
        "required": ["file_path", "edits"],
    }

    def __init__(self, workspace: Path, containment: ContainmentMode = "strict") -> None:
        self._workspace = workspace
        self._containment = containment

    async def execute(  # type: ignore[override]
        self, edits: list[dict[str, Any]], file_path: str, **kwargs
    ) -> ToolResult:
        try:
            resolved = resolve_path(file_path, self._workspace, self._containment)
        except WorkspaceEscapeError as exc:
            logger.warning("edit_file containment violation: %s", exc)
            return self._error(str(exc))

        if not resolved.exists():
            return self._error(f"File not found: {file_path}")

        if not isinstance(edits, list) or not edits:
            return self._error("'edits' must be a non-empty list")

        try:
            original_content = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("edit_file lectura imposible %s: %s", resolved, exc)
            return self._error(f"Cannot read file: {exc}")

        has_trailing_newline = original_content.endswith("\n")
        lines = original_content.splitlines()

        # Fase 1: validar shape de cada edit y comprobar que TODO el batch tiene matches
        # en el archivo original. No mutamos nada — feedback temprano al LLM.
        for i, edit in enumerate(edits):
            err = self._validate_edit(i, edit)
            if err is not None:
                return self._error(err)
            try:
                matched = self._find_matches(edit, lines)
            except re.error as exc:
                return self._error(f"Edit {i}: invalid regex pattern: {exc}")
            if not matched:
                return self._error(
                    f"Edit {i}: pattern {edit['pattern']!r} did not match any line in file"
                )

        # Fase 2: aplicar en orden sobre una copia. Re-calculamos matches sobre el estado
        # mutado (un edit anterior pudo consumir las líneas que el siguiente buscaba) y,
        # si en algún punto no hay matches, abortamos sin escribir → todo-o-nada real.
        new_lines = list(lines)
        for i, edit in enumerate(edits):
            current_matches = self._find_matches(edit, new_lines)
            if not current_matches:
                return self._error(
                    f"Edit {i}: pattern {edit['pattern']!r} matched in the original file "
                    "but no matches remain after applying previous edits — batch reverted"
                )
            count = edit.get("count", 1)
            if count is None:
                count = 1
            if count > 0:
                current_matches = current_matches[:count]
            new_lines = self._apply_edit(edit, new_lines, current_matches)

        # Reconstruir respetando el trailing newline original.
        if new_lines:
            final_content = "\n".join(new_lines)
            if has_trailing_newline:
                final_content += "\n"
        else:
            final_content = ""

        # Escritura atómica (mismo patrón que patch_file / write_file).
        temp_path = resolved.with_suffix(f".tmp.{uuid.uuid4().hex}{resolved.suffix}")
        try:
            temp_path.write_text(final_content, encoding="utf-8")
            temp_path.rename(resolved)
        except OSError as exc:
            logger.error("edit_file escritura atómica falló para %s: %s", resolved, exc)
            if temp_path.exists():
                with contextlib.suppress(OSError):
                    temp_path.unlink()
            return self._error(f"Write failed: {exc}")

        logger.info("edit_file %d edits aplicados sobre %s", len(edits), resolved)
        payload = {"success": True, "path": str(resolved)}
        return ToolResult(
            tool_name=self.name,
            output=json.dumps(payload, ensure_ascii=False),
            success=True,
        )

    # -----------------------------
    # Helpers
    # -----------------------------

    @staticmethod
    def _validate_edit(i: int, edit: dict[str, Any]) -> str | None:
        if not isinstance(edit, dict):
            return f"Edit {i}: must be an object"
        op = edit.get("op")
        if op not in _VALID_OPS:
            return f"Edit {i}: invalid op {op!r}; expected one of {list(_VALID_OPS)}"
        pattern = edit.get("pattern")
        if not isinstance(pattern, str) or pattern == "":
            return f"Edit {i}: 'pattern' must be a non-empty string"
        if op == "replace" and not isinstance(edit.get("replacement"), str):
            return f"Edit {i}: op='replace' requires 'replacement' (string)"
        if op in ("insert_before", "insert_after") and not isinstance(edit.get("content"), str):
            return f"Edit {i}: op={op!r} requires 'content' (string)"
        count = edit.get("count", 1)
        if count is not None and (not isinstance(count, int) or count < 0):
            return f"Edit {i}: 'count' must be a non-negative integer (got {count!r})"
        return None

    @staticmethod
    def _find_matches(edit: dict[str, Any], lines: list[str]) -> list[int]:
        pattern = edit["pattern"]
        is_regex = bool(edit.get("is_regex", False))
        if is_regex:
            compiled = re.compile(pattern)
            return [i for i, line in enumerate(lines) if compiled.search(line)]
        return [i for i, line in enumerate(lines) if pattern in line]

    @staticmethod
    def _apply_edit(edit: dict[str, Any], lines: list[str], match_indices: list[int]) -> list[str]:
        op = edit["op"]
        is_regex = bool(edit.get("is_regex", False))
        pattern = edit["pattern"]

        if op == "replace":
            replacement = edit["replacement"]
            for idx in match_indices:
                if is_regex:
                    lines[idx] = re.sub(pattern, replacement, lines[idx], count=1)
                else:
                    lines[idx] = lines[idx].replace(pattern, replacement, 1)
            return lines

        # insert/delete cambian la longitud → iteramos descendente para no perder índices.
        if op == "delete_lines":
            for idx in sorted(match_indices, reverse=True):
                del lines[idx]
            return lines

        # insert_before / insert_after — content puede ser multilínea.
        content = edit["content"]
        content_lines = content.split("\n") if content else [""]
        if op == "insert_before":
            for idx in sorted(match_indices, reverse=True):
                lines[idx:idx] = content_lines
        else:  # insert_after
            for idx in sorted(match_indices, reverse=True):
                lines[idx + 1 : idx + 1] = content_lines
        return lines

    def _error(self, message: str) -> ToolResult:
        payload = {"success": False, "error": message}
        return ToolResult(
            tool_name=self.name,
            output=json.dumps(payload, ensure_ascii=False),
            success=False,
            error=message,
        )
