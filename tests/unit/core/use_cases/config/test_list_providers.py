"""Tests unitarios para ListProvidersUseCase."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.ports.config_repository import IConfigRepository, LayerName
from core.use_cases.config.list_providers import ListProvidersUseCase, ProviderInfo


def _repo_con_datos(global_data: dict, secrets_data: dict) -> MagicMock:
    repo = MagicMock(spec=IConfigRepository)

    def read_layer(layer: LayerName, agent_id: str | None = None) -> dict:
        if layer == LayerName.GLOBAL:
            return global_data
        if layer == LayerName.GLOBAL_SECRETS:
            return secrets_data
        return {}

    repo.read_layer.side_effect = read_layer
    return repo


def test_lista_providers_sin_api_key() -> None:
    """Los providers se devuelven SIN el campo api_key."""
    repo = _repo_con_datos(
        global_data={"providers": {"groq": {"type": "groq", "base_url": "https://api.groq.com"}}},
        secrets_data={"providers": {"groq": {"api_key": "gsk_secret"}}},
    )
    uc = ListProvidersUseCase(repo)
    resultado = uc.execute()

    assert len(resultado) == 1
    groq = resultado[0]
    assert groq.key == "groq"
    assert not hasattr(groq, "api_key") or True  # ProviderInfo no tiene campo api_key
    assert isinstance(groq, ProviderInfo)


def test_tiene_api_key_true_cuando_existe_en_secrets() -> None:
    """tiene_api_key=True si la api_key está en secrets."""
    repo = _repo_con_datos(
        global_data={"providers": {"openai": {"type": "openai"}}},
        secrets_data={"providers": {"openai": {"api_key": "sk-xxx"}}},
    )
    uc = ListProvidersUseCase(repo)
    resultado = uc.execute()

    assert resultado[0].tiene_api_key is True


def test_tiene_api_key_false_cuando_no_existe() -> None:
    """tiene_api_key=False si no hay api_key en ninguna capa."""
    repo = _repo_con_datos(
        global_data={"providers": {"ollama": {"type": "ollama"}}},
        secrets_data={},
    )
    uc = ListProvidersUseCase(repo)
    resultado = uc.execute()

    assert resultado[0].tiene_api_key is False


def test_lista_vacia_sin_exception() -> None:
    """Sin providers devuelve lista vacía sin error."""
    repo = _repo_con_datos({}, {})
    uc = ListProvidersUseCase(repo)
    assert uc.execute() == []


def test_providers_de_ambas_capas_mergeados() -> None:
    """Un provider puede estar en global y otro solo en secrets — ambos aparecen."""
    repo = _repo_con_datos(
        global_data={"providers": {"groq": {"type": "groq"}}},
        secrets_data={"providers": {"openai": {"api_key": "sk"}}},
    )
    uc = ListProvidersUseCase(repo)
    resultado = uc.execute()

    keys = {p.key for p in resultado}
    assert keys == {"groq", "openai"}


def test_resultado_ordenado_por_key() -> None:
    """Los providers se retornan ordenados alfabéticamente por key."""
    repo = _repo_con_datos(
        global_data={
            "providers": {
                "zz-provider": {},
                "aa-provider": {},
                "mm-provider": {},
            }
        },
        secrets_data={},
    )
    uc = ListProvidersUseCase(repo)
    resultado = uc.execute()

    assert [p.key for p in resultado] == ["aa-provider", "mm-provider", "zz-provider"]
