"""Tests de ``infrastructure/home.py`` — resolución del home de instancia.

Cubre los tres niveles de precedencia (override explícito → env ``INAKI_HOME`` →
default ``~/.inaki``), el reset del override y la expansión de ``~``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.home import get_inaki_home, set_inaki_home


@pytest.fixture(autouse=True)
def _reset_home_override():
    """El override es global de proceso: lo limpiamos antes y después de cada test
    para que no se filtre estado entre casos."""
    set_inaki_home(None)
    yield
    set_inaki_home(None)


def test_default_sin_override_ni_env(monkeypatch):
    monkeypatch.delenv("INAKI_HOME", raising=False)
    assert get_inaki_home() == Path.home() / ".inaki"


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("INAKI_HOME", str(tmp_path))
    assert get_inaki_home() == tmp_path


def test_override_explicito_gana_sobre_env(monkeypatch, tmp_path):
    desde_env = tmp_path / "desde_env"
    explicito = tmp_path / "explicito"
    monkeypatch.setenv("INAKI_HOME", str(desde_env))
    set_inaki_home(explicito)
    assert get_inaki_home() == explicito


def test_set_none_limpia_override(monkeypatch, tmp_path):
    monkeypatch.delenv("INAKI_HOME", raising=False)
    set_inaki_home(tmp_path)
    assert get_inaki_home() == tmp_path
    set_inaki_home(None)
    assert get_inaki_home() == Path.home() / ".inaki"


def test_expanduser_en_override(monkeypatch):
    monkeypatch.delenv("INAKI_HOME", raising=False)
    set_inaki_home("~/mi-home-custom")
    assert get_inaki_home() == Path.home() / "mi-home-custom"


def test_validador_runtimepath_reancla_con_el_home(tmp_path, monkeypatch):
    """El BeforeValidator de RuntimePath ancla contra get_inaki_home() en runtime:
    un config construido FRESCO tras set_inaki_home cae bajo el nuevo home."""
    monkeypatch.delenv("INAKI_HOME", raising=False)
    set_inaki_home(tmp_path)
    from infrastructure import config_schema as cs

    assert cs.SchedulerConfig().db_filename == str(tmp_path / "data/scheduler.db")
    assert cs.MemoriesConfig().db_filename == str(tmp_path / "data/inaki.db")


def test_eager_defaults_con_runtimepath_usan_default_factory():
    """Guard anti-regresión del 'trap de import-time': los configs con RuntimePath
    usados como default de GlobalConfig DEBEN declararse con default_factory (no
    `= XConfig()`), o `--home` no relocalizaría sus DBs si faltan del YAML."""
    from infrastructure import config_schema as cs

    for campo in ("scheduler", "knowledge"):
        fld = cs.GlobalConfig.model_fields[campo]
        assert fld.default_factory is not None, (
            f"GlobalConfig.{campo} debe usar default_factory (ver trap de import-time)"
        )
