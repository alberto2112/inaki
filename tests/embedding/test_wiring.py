"""Tests de ``EmbeddingProviderFactory``: el provider inexistente es error del operador."""

from __future__ import annotations

import pytest

from inaki.config import EmbeddingConfig
from inaki.embedding.wiring import EmbeddingProviderFactory
from inaki.shared.errors import ConfigError


def test_create_provider_desconocido_es_config_error() -> None:
    """``ConfigError`` (no ``ValueError``): así ``borde_de_config`` lo imprime como
    mensaje accionable en el arranque y en el reload, en vez de un traceback."""
    cfg = EmbeddingConfig(provider="no-existe")

    with pytest.raises(ConfigError) as exc_info:
        EmbeddingProviderFactory.create(cfg, providers={})

    mensaje = str(exc_info.value)
    assert "no-existe" in mensaje
    assert "Disponibles" in mensaje
