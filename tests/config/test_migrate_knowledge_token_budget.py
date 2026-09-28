"""Migración one-shot que retira ``knowledge.token_budget_warn_threshold``.

El bootstrap renderiza ``global.yaml`` desde los defaults del schema, así que la
clave puede estar en cualquier instalación. Con ``extra="forbid"``, borrarla del
schema sin esta migración abortaba el arranque (``config-limpieza-final``).
"""

from __future__ import annotations

from pathlib import Path

from inaki.config.loader import load_global_config, migrate_knowledge_token_budget


def _global(tmp_path: Path, contenido: str) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "global.yaml").write_text(contenido, encoding="utf-8")
    return config_dir


def test_quita_la_clave_y_conserva_el_resto_del_bloque(tmp_path: Path) -> None:
    config_dir = _global(
        tmp_path,
        "knowledge:\n"
        "  enabled: true\n"
        "  token_budget_warn_threshold: 4000  # comentario del operador\n"
        "  max_total_chunks: 7\n",
    )

    migrate_knowledge_token_budget(config_dir)

    texto = (config_dir / "global.yaml").read_text(encoding="utf-8")
    assert "token_budget_warn_threshold" not in texto
    assert "max_total_chunks: 7" in texto, "lo que el operador declaró se conserva"
    global_cfg, _ = load_global_config(config_dir)
    assert global_cfg.knowledge.max_total_chunks == 7


def test_sin_la_clave_no_reescribe_el_fichero(tmp_path: Path) -> None:
    original = "knowledge:\n  enabled: true   # formato raro que no se toca\n"
    config_dir = _global(tmp_path, original)

    migrate_knowledge_token_budget(config_dir)

    assert (config_dir / "global.yaml").read_text(encoding="utf-8") == original


def test_sin_global_yaml_no_hace_nada(tmp_path: Path) -> None:
    migrate_knowledge_token_budget(tmp_path / "no-existe")
