"""Tests del schema del registry de proveedores."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from infrastructure.config import ProviderConfig


def test_provider_config_acepta_fields_validos() -> None:
    cfg = ProviderConfig(api_key="K", base_url="https://x/v1")
    assert cfg.api_key == "K"
    assert cfg.base_url == "https://x/v1"
    assert cfg.type is None


def test_provider_config_type_opcional_default_none() -> None:
    """El ``type`` es None por default — el loader lo resuelve a la key."""
    cfg = ProviderConfig(api_key="K")
    assert cfg.type is None


def test_provider_config_type_explicito_para_multi_instancia() -> None:
    """``type: groq`` permite entradas como ``groq-work`` apuntando al adapter groq."""
    cfg = ProviderConfig(type="groq", api_key="K2")
    assert cfg.type == "groq"


def test_provider_config_sin_api_key_ok_para_locales() -> None:
    """Providers locales (ollama, e5_onnx) no requieren api_key."""
    cfg = ProviderConfig()
    assert cfg.api_key is None
    assert cfg.base_url is None


def test_provider_config_rechaza_fields_desconocidos() -> None:
    """``extra=forbid`` atrapa typos temprano."""
    with pytest.raises(ValidationError):
        ProviderConfig(api_ky="K")  # type: ignore[call-arg]
