"""Tests unitarios para DeleteProviderUseCase."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.ports.config_repository import IConfigRepository, LayerName
from core.use_cases.config.delete_provider import DeleteProviderUseCase


def _repo_con_providers(global_providers: dict, secrets_providers: dict) -> MagicMock:
    repo = MagicMock(spec=IConfigRepository)

    def read_layer(layer: LayerName, agent_id: str | None = None) -> dict:
        if layer == LayerName.GLOBAL:
            return {"providers": dict(global_providers)}
        if layer == LayerName.GLOBAL_SECRETS:
            return {"providers": dict(secrets_providers)}
        return {}

    repo.read_layer.side_effect = read_layer
    return repo


def test_elimina_provider_de_global(repo: MagicMock = None) -> None:
    """El provider se elimina de global.yaml."""
    repo = _repo_con_providers(
        {"groq": {"type": "groq"}, "openai": {"type": "openai"}},
        {},
    )
    uc = DeleteProviderUseCase(repo)
    uc.execute("groq")

    escritura_global = next(
        c[0][1] for c in repo.write_layer.call_args_list if c[0][0] == LayerName.GLOBAL
    )
    assert "groq" not in escritura_global["providers"]
    assert "openai" in escritura_global["providers"]


def test_no_elimina_api_key_de_secrets_por_defecto() -> None:
    """Sin borrar_api_key=True, no se toca global.secrets.yaml."""
    repo = _repo_con_providers(
        {"groq": {"type": "groq"}},
        {"groq": {"api_key": "sk"}},
    )
    uc = DeleteProviderUseCase(repo)
    uc.execute("groq")

    capas_escritas = [c[0][0] for c in repo.write_layer.call_args_list]
    assert LayerName.GLOBAL_SECRETS not in capas_escritas


def test_elimina_api_key_de_secrets_cuando_se_pide() -> None:
    """Con borrar_api_key=True, también elimina de global.secrets.yaml."""
    repo = _repo_con_providers(
        {"groq": {"type": "groq"}},
        {"groq": {"api_key": "sk"}, "openai": {"api_key": "ok"}},
    )
    uc = DeleteProviderUseCase(repo)
    uc.execute("groq", borrar_api_key=True)

    escritura_secrets = next(
        c[0][1]
        for c in repo.write_layer.call_args_list
        if c[0][0] == LayerName.GLOBAL_SECRETS
    )
    assert "groq" not in escritura_secrets["providers"]
    assert "openai" in escritura_secrets["providers"]


def test_provider_inexistente_es_noop() -> None:
    """Si el provider no existe en ninguna capa, no lanza error."""
    repo = _repo_con_providers({}, {})
    uc = DeleteProviderUseCase(repo)
    uc.execute("no-existe")  # No debe lanzar excepción

    repo.write_layer.assert_called()
