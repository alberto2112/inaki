"""Tests unitarios de EditFileTool — ediciones por patrón, línea a línea."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters.outbound.tools.edit_file_tool import EditFileTool


@pytest.fixture
def tool(tmp_path: Path) -> EditFileTool:
    return EditFileTool(workspace=tmp_path, containment="strict")


def _payload(result) -> dict:
    return json.loads(result.output)


# -----------------------------
# Casos felices
# -----------------------------


async def test_replace_literal_first_match_only(tool: EditFileTool, tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo bar\nfoo baz\nfoo qux\n", encoding="utf-8")

    result = await tool.execute(
        file_path="f.txt",
        edits=[{"op": "replace", "pattern": "foo", "replacement": "XX"}],
    )

    assert result.success is True
    assert f.read_text() == "XX bar\nfoo baz\nfoo qux\n"


async def test_replace_all_with_count_zero(tool: EditFileTool, tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo\nfoo\nfoo\n", encoding="utf-8")

    result = await tool.execute(
        file_path="f.txt",
        edits=[{"op": "replace", "pattern": "foo", "replacement": "bar", "count": 0}],
    )

    assert result.success is True
    assert f.read_text() == "bar\nbar\nbar\n"


async def test_replace_regex_with_backreference(tool: EditFileTool, tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("color: red;\n", encoding="utf-8")

    result = await tool.execute(
        file_path="f.txt",
        edits=[
            {
                "op": "replace",
                "pattern": r"color:\s*(\w+)",
                "replacement": r"background: \1",
                "is_regex": True,
            }
        ],
    )

    assert result.success is True
    assert f.read_text() == "background: red;\n"


async def test_replace_only_first_match_within_line(tool: EditFileTool, tmp_path: Path) -> None:
    """Una línea con varias ocurrencias del substring → solo se cambia la primera."""
    f = tmp_path / "f.txt"
    f.write_text("foo foo foo\n", encoding="utf-8")

    result = await tool.execute(
        file_path="f.txt",
        edits=[{"op": "replace", "pattern": "foo", "replacement": "X"}],
    )

    assert result.success is True
    assert f.read_text() == "X foo foo\n"


async def test_insert_before(tool: EditFileTool, tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("a\nb\nc\n", encoding="utf-8")

    result = await tool.execute(
        file_path="f.txt",
        edits=[{"op": "insert_before", "pattern": "b", "content": "---"}],
    )

    assert result.success is True
    assert f.read_text() == "a\n---\nb\nc\n"


async def test_insert_after_multiline_content(tool: EditFileTool, tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("a\nb\nc\n", encoding="utf-8")

    result = await tool.execute(
        file_path="f.txt",
        edits=[{"op": "insert_after", "pattern": "b", "content": "x\ny"}],
    )

    assert result.success is True
    assert f.read_text() == "a\nb\nx\ny\nc\n"


async def test_delete_lines(tool: EditFileTool, tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("keep\nDROP me\nkeep\nDROP also\n", encoding="utf-8")

    result = await tool.execute(
        file_path="f.txt",
        edits=[{"op": "delete_lines", "pattern": "DROP", "count": 0}],
    )

    assert result.success is True
    assert f.read_text() == "keep\nkeep\n"


async def test_delete_all_lines_leaves_empty_file(tool: EditFileTool, tmp_path: Path) -> None:
    """Si se borran TODAS las líneas, el archivo queda vacío (no '\\n' espurio)."""
    f = tmp_path / "f.txt"
    f.write_text("a\nb\nc\n", encoding="utf-8")

    result = await tool.execute(
        file_path="f.txt",
        edits=[{"op": "delete_lines", "pattern": ".*", "count": 0, "is_regex": True}],
    )

    assert result.success is True
    assert f.read_text() == ""


async def test_batch_multiple_edits_in_order(tool: EditFileTool, tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    result = await tool.execute(
        file_path="f.txt",
        edits=[
            {"op": "replace", "pattern": "alpha", "replacement": "A"},
            {"op": "insert_after", "pattern": "beta", "content": "B-after"},
            {"op": "delete_lines", "pattern": "gamma"},
        ],
    )

    assert result.success is True
    assert f.read_text() == "A\nbeta\nB-after\n"


async def test_preserves_no_trailing_newline(tool: EditFileTool, tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo\nbar", encoding="utf-8")  # sin \n final

    result = await tool.execute(
        file_path="f.txt",
        edits=[{"op": "replace", "pattern": "foo", "replacement": "FOO"}],
    )

    assert result.success is True
    assert f.read_text() == "FOO\nbar"


# -----------------------------
# Errores y atomicidad
# -----------------------------


async def test_no_match_returns_error_without_writing(tool: EditFileTool, tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    original = "foo\nbar\n"
    f.write_text(original, encoding="utf-8")

    result = await tool.execute(
        file_path="f.txt",
        edits=[{"op": "replace", "pattern": "nope", "replacement": "X"}],
    )

    assert result.success is False
    assert "did not match" in (result.error or "")
    assert f.read_text() == original  # intacto


async def test_batch_aborts_if_any_pattern_missing(tool: EditFileTool, tmp_path: Path) -> None:
    """Si UN edit del batch no matchea, NADA se escribe."""
    f = tmp_path / "f.txt"
    original = "foo\nbar\n"
    f.write_text(original, encoding="utf-8")

    result = await tool.execute(
        file_path="f.txt",
        edits=[
            {"op": "replace", "pattern": "foo", "replacement": "FOO"},  # OK
            {"op": "delete_lines", "pattern": "missing"},  # FAIL
        ],
    )

    assert result.success is False
    assert f.read_text() == original  # rollback total


async def test_second_edit_loses_matches_to_first_aborts(
    tool: EditFileTool, tmp_path: Path
) -> None:
    """Si edit 1 consume las líneas que edit 2 necesitaba, abort + rollback."""
    f = tmp_path / "f.txt"
    original = "foo\nfoo\n"
    f.write_text(original, encoding="utf-8")

    result = await tool.execute(
        file_path="f.txt",
        edits=[
            {"op": "delete_lines", "pattern": "foo", "count": 0},  # borra ambas
            {"op": "replace", "pattern": "foo", "replacement": "X"},  # ya no hay foo
        ],
    )

    assert result.success is False
    assert "batch reverted" in (result.error or "")
    assert f.read_text() == original  # archivo intacto


async def test_file_not_found(tool: EditFileTool) -> None:
    result = await tool.execute(
        file_path="missing.txt",
        edits=[{"op": "replace", "pattern": "x", "replacement": "y"}],
    )
    assert result.success is False
    assert "File not found" in (result.error or "")


async def test_invalid_regex_returns_error(tool: EditFileTool, tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo\n", encoding="utf-8")

    result = await tool.execute(
        file_path="f.txt",
        edits=[{"op": "replace", "pattern": "[unclosed", "replacement": "x", "is_regex": True}],
    )

    assert result.success is False
    assert "invalid regex" in (result.error or "")


async def test_invalid_op_rejected(tool: EditFileTool, tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo\n", encoding="utf-8")

    result = await tool.execute(
        file_path="f.txt",
        edits=[{"op": "rewrite", "pattern": "foo"}],
    )
    assert result.success is False
    assert "invalid op" in (result.error or "")


async def test_empty_pattern_rejected(tool: EditFileTool, tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo\n", encoding="utf-8")

    result = await tool.execute(
        file_path="f.txt",
        edits=[{"op": "replace", "pattern": "", "replacement": "x"}],
    )
    assert result.success is False
    assert "non-empty string" in (result.error or "")


async def test_replace_missing_replacement_rejected(tool: EditFileTool, tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo\n", encoding="utf-8")

    result = await tool.execute(
        file_path="f.txt",
        edits=[{"op": "replace", "pattern": "foo"}],
    )
    assert result.success is False
    assert "requires 'replacement'" in (result.error or "")


async def test_insert_missing_content_rejected(tool: EditFileTool, tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo\n", encoding="utf-8")

    result = await tool.execute(
        file_path="f.txt",
        edits=[{"op": "insert_after", "pattern": "foo"}],
    )
    assert result.success is False
    assert "requires 'content'" in (result.error or "")


async def test_empty_edits_list_rejected(tool: EditFileTool, tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo\n", encoding="utf-8")

    result = await tool.execute(file_path="f.txt", edits=[])
    assert result.success is False


async def test_workspace_containment_strict(tmp_path: Path) -> None:
    """Path absoluto fuera del workspace → error de contención."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("foo\n", encoding="utf-8")

    tool = EditFileTool(workspace=workspace, containment="strict")
    result = await tool.execute(
        file_path=str(outside),
        edits=[{"op": "replace", "pattern": "foo", "replacement": "x"}],
    )

    assert result.success is False
    assert outside.read_text() == "foo\n"


# -----------------------------
# Output / schema
# -----------------------------


async def test_success_payload_shape(tool: EditFileTool, tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo\n", encoding="utf-8")

    result = await tool.execute(
        file_path="f.txt",
        edits=[{"op": "replace", "pattern": "foo", "replacement": "bar"}],
    )

    payload = _payload(result)
    assert payload == {"success": True, "path": str((tmp_path / "f.txt").resolve())}


def test_tool_name_and_schema_required_fields() -> None:
    assert EditFileTool.name == "edit_file"
    schema = EditFileTool.parameters_schema
    assert schema["required"] == ["file_path", "edits"]
    item_schema = schema["properties"]["edits"]["items"]
    assert set(item_schema["required"]) == {"op", "pattern"}
    assert set(item_schema["properties"]["op"]["enum"]) == {
        "replace",
        "insert_before",
        "insert_after",
        "delete_lines",
    }
