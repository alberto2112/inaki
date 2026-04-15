"""Tests para _render_default_global_yaml — bootstrap del YAML inicial."""

from __future__ import annotations

import yaml

from infrastructure.config import _render_default_global_yaml


def test_bootstrap_yaml_incluye_seccion_channel_fallback() -> None:
    rendered = _render_default_global_yaml()
    data = yaml.safe_load(rendered)
    assert "scheduler" in data
    assert "channel_fallback" in data["scheduler"]


def test_bootstrap_yaml_channel_fallback_tiene_default_y_overrides() -> None:
    rendered = _render_default_global_yaml()
    data = yaml.safe_load(rendered)
    cf = data["scheduler"]["channel_fallback"]
    assert "default" in cf
    assert "overrides" in cf


def test_bootstrap_yaml_channel_fallback_default_es_none_por_defecto() -> None:
    rendered = _render_default_global_yaml()
    data = yaml.safe_load(rendered)
    assert data["scheduler"]["channel_fallback"]["default"] is None


def test_bootstrap_yaml_channel_fallback_overrides_es_dict_vacio() -> None:
    rendered = _render_default_global_yaml()
    data = yaml.safe_load(rendered)
    assert data["scheduler"]["channel_fallback"]["overrides"] == {}
