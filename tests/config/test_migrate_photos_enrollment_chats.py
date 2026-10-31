"""Migración one-shot: ``photos.enrollment_chats`` → ``photos.faces.enrollment_chats``.

El campo decide si el agente ofrece enrolar caras desconocidas: es reconocimiento
facial, no escena. El bootstrap renderiza ``global.yaml`` desde los defaults del
schema, así que la clave vieja está en cualquier instalación; con ``extra="forbid"``
moverla sin esta migración abortaba el arranque (``config-limpieza-final``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from inaki.config.loader import load_global_config, migrate_photos_enrollment_chats
from inaki.shared.errors import ConfigError


def _global(tmp_path: Path, contenido: str) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "global.yaml").write_text(contenido, encoding="utf-8")
    return config_dir


def test_mueve_la_clave_a_faces_y_conserva_el_resto(tmp_path: Path) -> None:
    config_dir = _global(
        tmp_path,
        "photos:\n"
        "  enabled: true\n"
        "  enrollment_chats: none  # comentario del operador\n"
        "  faces:\n"
        "    match_threshold: 0.6\n",
    )

    migrate_photos_enrollment_chats(config_dir)

    global_cfg, _ = load_global_config(config_dir)
    assert global_cfg.photos is not None
    assert global_cfg.photos.faces.enrollment_chats == "none", "el valor del operador viaja"
    assert global_cfg.photos.faces.match_threshold == 0.6, "lo que ya estaba en faces queda"


def test_sin_bloque_faces_lo_crea(tmp_path: Path) -> None:
    config_dir = _global(tmp_path, "photos:\n  enrollment_chats: none\n")

    migrate_photos_enrollment_chats(config_dir)

    global_cfg, _ = load_global_config(config_dir)
    assert global_cfg.photos is not None
    assert global_cfg.photos.faces.enrollment_chats == "none"


def test_las_dos_claves_con_el_mismo_valor_deja_solo_la_nueva(tmp_path: Path) -> None:
    config_dir = _global(
        tmp_path,
        "photos:\n  enrollment_chats: private\n  faces:\n    enrollment_chats: private\n",
    )

    migrate_photos_enrollment_chats(config_dir)

    texto = (config_dir / "global.yaml").read_text(encoding="utf-8")
    assert texto.count("enrollment_chats") == 1


def test_las_dos_claves_con_valores_distintos_aborta_sin_elegir(tmp_path: Path) -> None:
    """Elegir una en silencio contradice al YAML (``config-falla-ruidoso``)."""
    original = "photos:\n  enrollment_chats: none\n  faces:\n    enrollment_chats: private\n"
    config_dir = _global(tmp_path, original)

    with pytest.raises(ConfigError) as exc:
        migrate_photos_enrollment_chats(config_dir)

    assert "photos.enrollment_chats" in str(exc.value)
    assert "photos.faces.enrollment_chats" in str(exc.value)
    assert (config_dir / "global.yaml").read_text(encoding="utf-8") == original


def test_sin_la_clave_no_reescribe_el_fichero(tmp_path: Path) -> None:
    original = "photos:\n  enabled: true   # formato raro que no se toca\n"
    config_dir = _global(tmp_path, original)

    migrate_photos_enrollment_chats(config_dir)

    assert (config_dir / "global.yaml").read_text(encoding="utf-8") == original


def test_el_arranque_la_aplica_solo(tmp_path: Path) -> None:
    """Una instalación existente arranca sin tocar nada a mano."""
    config_dir = _global(
        tmp_path,
        "app: {name: Test}\n"
        "llm: {provider: groq, model: m}\n"
        "embedding: {provider: e5_onnx, model_dirname: /tmp/m}\n"
        "photos:\n  enrollment_chats: none\n",
    )
    from inaki.config.loader import ensure_user_config

    ensure_user_config(config_dir, tmp_path / "agents")

    global_cfg, _ = load_global_config(config_dir)
    assert global_cfg.photos is not None
    assert global_cfg.photos.faces.enrollment_chats == "none"
