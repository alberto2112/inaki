"""Tests unitarios para UpsertProviderUseCase."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.ports.config_repository import IConfigRepository, LayerName
from core.use_cases.config.upsert_provider import UpsertProviderUseCase


@pytest.fixture()
def repo() -> MagicMock:
    repo = MagicMock(spec=IConfigRepository)
    # Why: side_effect con lambda para devolver instancias distintas en cada llamada.
    # Si se usa return_value={}, el mismo objeto dict es mutado por ambas lecturas
    # (global y secrets), rompiendo las assertions sobre el contenido por capa.
    repo.read_layer.side_effect = lambda *args, **kwargs: {}
    return repo


def test_api_key_va_a_secrets(repo: MagicMock) -> None:
    """La api_key siempre se escribe en GLOBAL_SECRETS, nunca en GLOBAL."""
    uc = UpsertProviderUseCase(repo)
    uc.execute("groq", type="groq", api_key="sk-secret")

    # Verificar que una de las escrituras fue en GLOBAL_SECRETS
    capas_escritas = [c[0][0] for c in repo.write_layer.call_args_list]
    assert LayerName.GLOBAL_SECRETS in capas_escritas

    # Y que el GLOBAL no tiene api_key
    escritura_global = next(
        (c[0][1] for c in repo.write_layer.call_args_list if c[0][0] == LayerName.GLOBAL),
        {},
    )
    providers_global = escritura_global.get("providers", {})
    assert "api_key" not in providers_global.get("groq", {})


def test_api_key_en_secrets_tiene_valor_correcto(repo: MagicMock) -> None:
    """El valor de api_key en secrets es el que se pasó."""
    uc = UpsertProviderUseCase(repo)
    uc.execute("openai", api_key="sk-openai-key")

    escritura_secrets = next(
        c[0][1]
        for c in repo.write_layer.call_args_list
        if c[0][0] == LayerName.GLOBAL_SECRETS
    )
    assert escritura_secrets["providers"]["openai"]["api_key"] == "sk-openai-key"


def test_sin_api_key_no_escribe_secrets(repo: MagicMock) -> None:
    """Si api_key=None, no se escribe en GLOBAL_SECRETS."""
    uc = UpsertProviderUseCase(repo)
    uc.execute("ollama", type="ollama")

    capas_escritas = [c[0][0] for c in repo.write_layer.call_args_list]
    assert LayerName.GLOBAL_SECRETS not in capas_escritas


def test_type_y_base_url_van_a_global(repo: MagicMock) -> None:
    """type y base_url se escriben en GLOBAL."""
    uc = UpsertProviderUseCase(repo)
    uc.execute("groq", type="groq", base_url="https://custom.groq.com")

    escritura_global = next(
        c[0][1]
        for c in repo.write_layer.call_args_list
        if c[0][0] == LayerName.GLOBAL
    )
    entrada = escritura_global["providers"]["groq"]
    assert entrada["type"] == "groq"
    assert entrada["base_url"] == "https://custom.groq.com"


def test_api_key_existente_no_se_copia_a_global(repo: MagicMock) -> None:
    """Si había api_key en global.yaml por error, se elimina al escribir."""
    import copy

    datos = {LayerName.GLOBAL: {"providers": {"groq": {"type": "groq", "api_key": "error!"}}}}
    repo.read_layer.side_effect = lambda layer, **_: copy.deepcopy(datos.get(layer, {}))

    uc = UpsertProviderUseCase(repo)
    uc.execute("groq", type="groq-v2")

    escritura_global = next(
        c[0][1]
        for c in repo.write_layer.call_args_list
        if c[0][0] == LayerName.GLOBAL
    )
    assert "api_key" not in escritura_global["providers"]["groq"]


def test_upsert_preserva_campos_no_modificados(repo: MagicMock) -> None:
    """Los campos existentes que no se pasan no se eliminan."""
    import copy

    datos = {
        LayerName.GLOBAL: {
            "providers": {"groq": {"type": "groq", "base_url": "https://old.url"}}
        }
    }
    repo.read_layer.side_effect = lambda layer, **_: copy.deepcopy(datos.get(layer, {}))

    uc = UpsertProviderUseCase(repo)
    # Solo actualizamos type, no base_url
    uc.execute("groq", type="groq-new")

    escritura_global = next(
        c[0][1]
        for c in repo.write_layer.call_args_list
        if c[0][0] == LayerName.GLOBAL
    )
    assert escritura_global["providers"]["groq"]["base_url"] == "https://old.url"
