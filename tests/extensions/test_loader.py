"""``descubrir_extensiones``: recorre ``app.ext_dirs`` e importa cada ``manifest.py``.

Solo descubrimiento: lo que devuelve son clases, paths y factories tal cual las
declara el manifest. Registrarlas es del composition root (ver
``test_container_extensions*.py``).
"""

from __future__ import annotations

import logging
import sys
import textwrap
from pathlib import Path

import pytest

from inaki.kernel.ports.outbound.tool_port import ITool, ToolResult
from inaki.extensions import Extension, descubrir_extensiones

_RAIZ_DEL_PROYECTO = Path(__file__).resolve().parents[2]


class _Tool(ITool):
    name = "una_tool"
    description = "tool de prueba"
    parameters_schema = {"type": "object", "properties": {}}

    async def execute(self, **kwargs) -> ToolResult:  # pragma: no cover
        return ToolResult(tool_name=self.name, output="ok", success=True)


def _manifest(ext_dir: Path, nombre: str, contenido: str) -> Path:
    directorio = ext_dir / nombre
    directorio.mkdir(parents=True, exist_ok=True)
    (directorio / "manifest.py").write_text(textwrap.dedent(contenido), encoding="utf-8")
    return directorio


@pytest.fixture(autouse=True)
def _limpiar_modulos_de_manifests():
    yield
    for k in [k for k in sys.modules if k.startswith("_inaki_ext_")]:
        del sys.modules[k]


def test_manifest_completo_se_devuelve_resuelto(tmp_path: Path) -> None:
    sys.modules["_test_mod_loader"] = type(sys)("_test_mod_loader")
    sys.modules["_test_mod_loader"].Tool = _Tool  # type: ignore[attr-defined]
    ext_dir = tmp_path / "ext"
    directorio = _manifest(
        ext_dir,
        "mi_ext",
        """\
        from _test_mod_loader import Tool
        TOOLS = [Tool]
        SKILLS = ["mi_ext.yaml"]
        KNOWLEDGE_SOURCES = [lambda a, g, e: None]
        """,
    )
    (directorio / "mi_ext.yaml").write_text("id: x\n", encoding="utf-8")

    [ext] = descubrir_extensiones([str(ext_dir)])

    assert ext == Extension(
        nombre="mi_ext",
        directorio=directorio,
        tools=(_Tool,),
        skills=(directorio / "mi_ext.yaml",),
        knowledge_sources=ext.knowledge_sources,
    )
    assert len(ext.knowledge_sources) == 1
    del sys.modules["_test_mod_loader"]


def test_manifest_vacio_o_con_none_es_una_extension_sin_nada(tmp_path: Path) -> None:
    """``TOOLS = None`` y ``SKILLS = []`` son manifests reales del árbol."""
    ext_dir = tmp_path / "ext"
    _manifest(ext_dir, "vacia", "")
    _manifest(ext_dir, "nula", "TOOLS = None\nSKILLS = []\n")

    exts = descubrir_extensiones([str(ext_dir)])

    assert [e.nombre for e in exts] == ["nula", "vacia"]  # orden alfabético estable
    assert all(e.tools == () and e.skills == () and e.knowledge_sources == () for e in exts)


def test_directorio_declarado_que_no_existe_avisa_y_sigue(tmp_path: Path, caplog) -> None:
    """Un recurso que la config nombra no se saltea en silencio."""
    ext_dir = tmp_path / "ext"
    _manifest(ext_dir, "buena", "TOOLS = []\n")
    ausente = tmp_path / "no_existe"

    with caplog.at_level(logging.WARNING):
        exts = descubrir_extensiones([str(ausente), str(ext_dir)])

    assert [e.nombre for e in exts] == ["buena"]
    assert str(ausente) in caplog.text and "app.ext_dirs" in caplog.text


def test_manifest_roto_se_salta_con_warning_y_el_resto_carga(tmp_path: Path, caplog) -> None:
    ext_dir = tmp_path / "ext"
    _manifest(ext_dir, "rota", "esto no es python !!!")
    _manifest(ext_dir, "sin_dependencia", "from modulo.que.no.existe import X\n")
    _manifest(ext_dir, "sana", "TOOLS = []\n")

    with caplog.at_level(logging.WARNING):
        exts = descubrir_extensiones([str(ext_dir)])

    assert [e.nombre for e in exts] == ["sana"]
    assert "'rota'" in caplog.text and "'sin_dependencia'" in caplog.text


def test_skill_declarada_que_no_existe_se_omite_con_warning(tmp_path: Path, caplog) -> None:
    ext_dir = tmp_path / "ext"
    directorio = _manifest(ext_dir, "ext", 'SKILLS = ["existe.yaml", "falta.yaml"]\n')
    (directorio / "existe.yaml").write_text("id: x\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        [ext] = descubrir_extensiones([str(ext_dir)])

    assert ext.skills == (directorio / "existe.yaml",)
    assert "falta.yaml" in caplog.text


def test_el_padre_del_directorio_entra_a_sys_path(tmp_path: Path) -> None:
    """Los imports internos de una extensión (``from ext.x.engine import …``) resuelven."""
    ext_dir = tmp_path / "raiz" / "ext"
    directorio = _manifest(
        ext_dir, "con_engine", "from ext.con_engine.engine import VALOR\nTOOLS = []\n"
    )
    (directorio / "__init__.py").write_text("", encoding="utf-8")
    (ext_dir / "__init__.py").write_text("", encoding="utf-8")
    (directorio / "engine.py").write_text("VALOR = 1\n", encoding="utf-8")
    sys.modules.pop("ext", None)
    try:
        [ext] = descubrir_extensiones([str(ext_dir)])
        assert ext.nombre == "con_engine"
        assert str(tmp_path / "raiz") in sys.path
    finally:
        sys.path.remove(str(tmp_path / "raiz"))
        sys.modules.pop("ext", None)
        sys.modules.pop("ext.con_engine", None)
        sys.modules.pop("ext.con_engine.engine", None)


@pytest.mark.skipif(
    not (_RAIZ_DEL_PROYECTO / "ext").is_dir(),
    reason="smoke local: ext/ del proyecto no está en esta máquina (gitignorado)",
)
def test_smoke_las_extensiones_reales_de_esta_maquina_cargan(caplog) -> None:
    """Cada ``ext/<x>/manifest.py`` del árbol carga y declara tools ``ITool``.

    Es el contrato que 15 extensiones reales dependen de que NO cambie con el
    refactor. Se salta en un clon limpio, donde ``ext/`` no existe.
    """
    ext_dir = _RAIZ_DEL_PROYECTO / "ext"
    esperadas = sorted(p.parent.name for p in ext_dir.glob("*/manifest.py"))

    with caplog.at_level(logging.WARNING):
        exts = descubrir_extensiones([str(ext_dir)])

    assert [e.nombre for e in exts] == esperadas, caplog.text
    for ext in exts:
        assert all(isinstance(t, type) and issubclass(t, ITool) for t in ext.tools), ext.nombre
        assert all(p.is_file() for p in ext.skills), ext.nombre
