"""Tests para AgentContainer._register_extensions()."""

from __future__ import annotations

import sys
import textwrap
import types
from pathlib import Path

import pytest

from adapters.outbound.skills.yaml_skill_repo import YamlSkillRepository
from adapters.outbound.tools.tool_registry import ToolRegistry
from core.ports.outbound.tool_port import ITool, ToolResult
from infrastructure.container import AgentContainer


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeEmbedder:
    async def embed_passage(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


class FakeTool(ITool):
    name = "fake_tool"
    description = "Fake tool for testing"
    parameters_schema = {"type": "object", "properties": {}}

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(tool_name=self.name, output="ok", success=True)


class FailingTool(ITool):
    name = "failing_tool"
    description = "Always fails on instantiation"
    parameters_schema = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        raise RuntimeError("instantiation error")

    async def execute(self, **kwargs) -> ToolResult:  # pragma: no cover
        return ToolResult(tool_name=self.name, output="", success=False)


# ---------------------------------------------------------------------------
# Fixture: container mínimo con _tools y _skills pero sin __init__ pesado
# ---------------------------------------------------------------------------

def _make_container(tmp_path: Path) -> AgentContainer:
    """Crea un AgentContainer con _tools y _skills falsos sin __init__ completo."""
    container = AgentContainer.__new__(AgentContainer)
    container._tools = ToolRegistry(embedder=FakeEmbedder())
    container._skills = YamlSkillRepository(FakeEmbedder())
    return container


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_manifest(ext_dir: Path, name: str, content: str) -> Path:
    pkg_dir = ext_dir / name
    pkg_dir.mkdir(parents=True, exist_ok=True)
    manifest = pkg_dir / "manifest.py"
    manifest.write_text(content, encoding="utf-8")
    return manifest


def _write_skill_yaml(ext_dir: Path, ext_name: str, filename: str, skill_id: str) -> Path:
    skill_path = ext_dir / ext_name / filename
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(
        textwrap.dedent(f"""\
            id: "{skill_id}"
            name: "Skill {skill_id}"
            description: "Test skill"
            instructions: ""
            tags: []
        """),
        encoding="utf-8",
    )
    return skill_path


# ---------------------------------------------------------------------------
# Cleanup sys.modules entre tests para evitar contaminación
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_inaki_ext_modules():
    before = set(sys.modules.keys())
    yield
    to_remove = [k for k in sys.modules if k.startswith("_inaki_ext_")]
    for k in to_remove:
        del sys.modules[k]
    # Remove only modules added during the test that start with _inaki_ext_


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_missing_dir_no_error(tmp_path: Path) -> None:
    """Directorio inexistente → no error, no tools registradas."""
    container = _make_container(tmp_path)
    container._register_extensions([str(tmp_path / "nonexistent")])
    assert len(container._tools._tools) == 0


def test_happy_path_tool_and_skill(tmp_path: Path, monkeypatch) -> None:
    """Manifest con TOOLS + SKILLS → tool registrada, add_file llamado."""
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()

    # Necesitamos que FakeTool sea importable desde el manifest — inyectamos el módulo
    fake_mod = types.ModuleType("_test_fake_tool_mod")
    fake_mod.FakeTool = FakeTool
    sys.modules["_test_fake_tool_mod"] = fake_mod

    skill_file = _write_skill_yaml(ext_dir, "myext", "myext.yaml", "my_skill")
    _write_manifest(
        ext_dir, "myext",
        "from _test_fake_tool_mod import FakeTool\n"
        "TOOLS = [FakeTool]\n"
        "SKILLS = ['myext.yaml']\n",
    )

    container = _make_container(tmp_path)
    container._register_extensions([str(ext_dir)])

    assert "fake_tool" in container._tools._tools
    assert skill_file.resolve() in [p.resolve() for p in container._skills._extra_files]

    del sys.modules["_test_fake_tool_mod"]


def test_manifest_syntax_error_skipped(tmp_path: Path) -> None:
    """Manifest con SyntaxError → WARNING, otras extensiones procesan."""
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()

    _write_manifest(ext_dir, "broken", "this is not valid python !!!")
    _write_manifest(ext_dir, "good", "TOOLS = []\nSKILLS = []\n")

    container = _make_container(tmp_path)
    container._register_extensions([str(ext_dir)])
    # 'broken' se skipea pero no explota
    assert len(container._tools._tools) == 0


def test_manifest_import_error_skipped(tmp_path: Path) -> None:
    """Manifest con ImportError → WARNING, no crash."""
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()

    _write_manifest(ext_dir, "broken", "from totally.nonexistent.module import Foo\nTOOLS = [Foo]\n")

    container = _make_container(tmp_path)
    container._register_extensions([str(ext_dir)])
    assert len(container._tools._tools) == 0


def test_empty_manifest_no_crash(tmp_path: Path) -> None:
    """Manifest vacío (sin TOOLS/SKILLS) → no crash."""
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()

    _write_manifest(ext_dir, "empty", "")

    container = _make_container(tmp_path)
    container._register_extensions([str(ext_dir)])
    assert len(container._tools._tools) == 0


def test_tool_instantiation_error_skipped(tmp_path: Path) -> None:
    """ToolClass() lanza excepción → WARNING, no aborta."""
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()

    fail_mod = types.ModuleType("_test_failing_tool_mod")
    fail_mod.FailingTool = FailingTool
    sys.modules["_test_failing_tool_mod"] = fail_mod

    _write_manifest(
        ext_dir, "badtool",
        "from _test_failing_tool_mod import FailingTool\n"
        "TOOLS = [FailingTool]\n"
        "SKILLS = []\n",
    )

    container = _make_container(tmp_path)
    container._register_extensions([str(ext_dir)])
    assert "failing_tool" not in container._tools._tools

    del sys.modules["_test_failing_tool_mod"]


def test_missing_skill_file_warning(tmp_path: Path, caplog) -> None:
    """YAML declarado no existe → WARNING, no añade skill."""
    import logging

    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()

    fake_mod = types.ModuleType("_test_fake_tool_mod2")
    fake_mod.FakeTool = FakeTool
    sys.modules["_test_fake_tool_mod2"] = fake_mod

    _write_manifest(
        ext_dir, "myext",
        "from _test_fake_tool_mod2 import FakeTool\n"
        "TOOLS = [FakeTool]\n"
        "SKILLS = ['nonexistent.yaml']\n",
    )

    container = _make_container(tmp_path)
    with caplog.at_level(logging.WARNING):
        container._register_extensions([str(ext_dir)])

    assert "nonexistent.yaml" in caplog.text
    assert len(container._skills._extra_files) == 0

    del sys.modules["_test_fake_tool_mod2"]


def test_name_collision_warning(tmp_path: Path, caplog) -> None:
    """Tool con nombre ya registrado → WARNING, built-in intacto."""
    import logging

    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()

    # Pre-registrar FakeTool como "built-in"
    fake_mod = types.ModuleType("_test_fake_tool_mod3")
    fake_mod.FakeTool = FakeTool
    sys.modules["_test_fake_tool_mod3"] = fake_mod

    _write_manifest(
        ext_dir, "collision_ext",
        "from _test_fake_tool_mod3 import FakeTool\n"
        "TOOLS = [FakeTool]\n"
        "SKILLS = []\n",
    )

    container = _make_container(tmp_path)
    original = FakeTool()
    container._tools.register(original)

    with caplog.at_level(logging.WARNING):
        container._register_extensions([str(ext_dir)])

    assert "colisión" in caplog.text
    assert container._tools._tools["fake_tool"] is original  # original intacto

    del sys.modules["_test_fake_tool_mod3"]


def test_multiple_dirs_order(tmp_path: Path) -> None:
    """Dos dirs, extensiones en cada uno → ambas registradas en orden."""
    dir1 = tmp_path / "ext1"
    dir2 = tmp_path / "ext2"
    dir1.mkdir()
    dir2.mkdir()

    class ToolA(ITool):
        name = "tool_a"
        description = "Tool A"
        parameters_schema = {"type": "object", "properties": {}}

        async def execute(self, **kwargs) -> ToolResult:
            return ToolResult(tool_name=self.name, output="a", success=True)

    class ToolB(ITool):
        name = "tool_b"
        description = "Tool B"
        parameters_schema = {"type": "object", "properties": {}}

        async def execute(self, **kwargs) -> ToolResult:
            return ToolResult(tool_name=self.name, output="b", success=True)

    mod_a = types.ModuleType("_test_tool_a_mod")
    mod_a.ToolA = ToolA
    sys.modules["_test_tool_a_mod"] = mod_a

    mod_b = types.ModuleType("_test_tool_b_mod")
    mod_b.ToolB = ToolB
    sys.modules["_test_tool_b_mod"] = mod_b

    _write_manifest(dir1, "ext_a", "from _test_tool_a_mod import ToolA\nTOOLS = [ToolA]\nSKILLS = []\n")
    _write_manifest(dir2, "ext_b", "from _test_tool_b_mod import ToolB\nTOOLS = [ToolB]\nSKILLS = []\n")

    container = _make_container(tmp_path)
    container._register_extensions([str(dir1), str(dir2)])

    assert "tool_a" in container._tools._tools
    assert "tool_b" in container._tools._tools

    del sys.modules["_test_tool_a_mod"]
    del sys.modules["_test_tool_b_mod"]
