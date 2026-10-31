"""``photos.scene`` se pisa por agente; el resto de ``photos`` es del arnés.

La escena (vendor, modelo, prompt, credencial) ya se construía POR AGENTE —
``build_photos_for_agent`` arma un describer por agente—, pero se alimentaba solo
del global: un ``photos.scene`` en ``agents/{id}.yaml`` abortaba la carga como
"config de nivel GLOBAL". ``faces`` (un solo modelo en RAM, un solo ``faces.db``),
``dedup`` (un solo job) y ``enabled`` (los singletons se cargan o no) siguen
siendo del arnés: declararlos en un agente aborta nombrando EL CAMPO, no el bloque.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from inaki.config import load_agent_config, load_global_config
from inaki.config.loader import campos_solo_globales
from inaki.config.runtime_overrides import destino_de
from inaki.perception.adapters.scene.deepseek_describer import DeepSeekSceneDescriberAdapter
from inaki.perception.wiring import PhotosSingletons, build_photos_for_agent
from inaki.shared.errors import ConfigError

_GLOBAL = (
    "app: {name: Test}\n"
    "llm: {provider: groq, model: m}\n"
    "embedding: {provider: e5_onnx, model_dirname: /tmp/m}\n"
    "memories: {db_filename: ':memory:'}\n"
    "chat_history: {db_filename: /tmp/h.db}\n"
    "photos:\n"
    "  scene: {provider: anthropic, model: claude-haiku, prompt_template: global}\n"
    "  faces: {model: buffalo_sc}\n"
)
_AGENTE = "id: dev\nname: Dev\ndescription: d\n"


def _home(tmp_path: Path, agente: str, global_yaml: str = _GLOBAL) -> tuple[Path, Path]:
    cfg_dir, agents_dir = tmp_path / "config", tmp_path / "agents"
    cfg_dir.mkdir()
    agents_dir.mkdir()
    (cfg_dir / "global.yaml").write_text(global_yaml, encoding="utf-8")
    (agents_dir / "dev.yaml").write_text(agente, encoding="utf-8")
    return cfg_dir, agents_dir


def _cargar(tmp_path: Path, agente: str, global_yaml: str = _GLOBAL, runtime=None):
    cfg_dir, agents_dir = _home(tmp_path, agente, global_yaml)
    _, global_raw = load_global_config(cfg_dir)
    return load_agent_config("dev", agents_dir, global_raw, runtime=runtime)


# ---------------------------------------------------------------------------
# Carga: el agente pisa campo a campo la escena del global
# ---------------------------------------------------------------------------


def test_el_agente_pisa_la_escena_campo_a_campo(tmp_path: Path) -> None:
    cfg = _cargar(tmp_path, _AGENTE + "photos:\n  scene: {prompt_template: federico}\n")

    assert cfg is not None and cfg.photos is not None
    assert cfg.photos.scene.prompt_template == "federico"
    # Lo que no declaró lo hereda del global: el merge de siempre.
    assert cfg.photos.scene.provider == "anthropic"
    assert cfg.photos.scene.model == "claude-haiku"


def test_sin_bloque_el_agente_hereda_la_escena_del_global(tmp_path: Path) -> None:
    cfg = _cargar(tmp_path, _AGENTE)

    assert cfg is not None and cfg.photos is not None
    assert cfg.photos.scene.prompt_template == "global"


def test_sin_photos_en_ningun_nivel_el_agente_no_inventa_el_bloque(tmp_path: Path) -> None:
    """``None`` como ``transcription``: un default acá aparecería en ``config show``
    como config efectiva de un pipeline que no existe."""
    sin_photos = _GLOBAL.split("photos:")[0]
    cfg = _cargar(tmp_path, _AGENTE, sin_photos)

    assert cfg is not None
    assert cfg.photos is None


@pytest.mark.parametrize(
    "bloque, campo",
    [
        ("faces:\n    model: buffalo_l\n", "faces"),
        ("dedup:\n    enabled: false\n", "dedup"),
        ("enabled: false\n", "enabled"),
    ],
)
def test_lo_del_arnes_en_un_agente_aborta_nombrando_el_campo(
    tmp_path: Path, bloque: str, campo: str
) -> None:
    with pytest.raises(ConfigError) as exc:
        _cargar(tmp_path, _AGENTE + "photos:\n  " + bloque)

    mensaje = str(exc.value)
    assert "dev.yaml" in mensaje, "el operador necesita saber qué fichero abrir"
    assert f"photos.{campo}" in mensaje, "se nombra el campo, no el bloque entero"
    assert "GLOBAL" in mensaje and "config/global.yaml" in mensaje
    assert "photos.scene" in mensaje, "hay que decir qué SÍ se puede pisar por agente"


def test_un_typo_bajo_photos_del_agente_no_se_traga(tmp_path: Path) -> None:
    """Filtrar los campos del arnés no puede volverse un filtro que tira lo desconocido."""
    with pytest.raises(ConfigError) as exc:
        _cargar(tmp_path, _AGENTE + "photos:\n  scen:\n    model: x\n")

    assert "scen" in str(exc.value)


def test_un_override_de_runtime_del_arnes_en_un_agente_aborta(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        _cargar(tmp_path, _AGENTE, runtime={"photos": {"faces": {"model": "buffalo_l"}}})

    assert "photos.faces" in str(exc.value)


# ---------------------------------------------------------------------------
# Derivado del schema, no de una lista
# ---------------------------------------------------------------------------


def test_los_campos_solo_globales_salen_del_schema() -> None:
    assert campos_solo_globales("photos") == frozenset({"enabled", "faces", "dedup"})
    # Bloques donde la vista del agente cubre todo el global: nada que filtrar.
    assert campos_solo_globales("delegation") == frozenset()
    assert campos_solo_globales("llm") == frozenset()


@pytest.mark.parametrize(
    "path, destino",
    [
        ("photos.scene.model", "agente"),
        ("photos.faces.model", "global"),
        ("photos.dedup.schedule", "global"),
        ("llm.model", "agente"),
        ("scheduler.enabled", "global"),
    ],
)
def test_config_set_manda_cada_campo_de_photos_a_su_capa(path: str, destino: str) -> None:
    """Un ``set`` de ``photos.faces.*`` en la capa del agente se filtraría en silencio."""
    assert destino_de(path) == destino


# ---------------------------------------------------------------------------
# Wiring: el describer de cada agente sale de SU escena
# ---------------------------------------------------------------------------


def test_el_describer_del_agente_usa_su_escena_y_no_la_del_global(tmp_path: Path) -> None:
    cfg_dir, agents_dir = _home(
        tmp_path,
        _AGENTE + "photos:\n  scene: {provider: deepseek, model: deepseek-flash, api_key: k}\n",
    )
    global_cfg, global_raw = load_global_config(cfg_dir)
    agente = load_agent_config("dev", agents_dir, global_raw)
    assert agente is not None and global_cfg.photos is not None

    armado = build_photos_for_agent(
        agente,
        global_cfg.photos,
        PhotosSingletons(vision=MagicMock(), face_registry=MagicMock()),
        get_channel_context=lambda: None,
    )

    describer = armado.process_photo._scene_describer
    assert isinstance(describer, DeepSeekSceneDescriberAdapter), "el global dice anthropic"
