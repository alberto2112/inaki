"""Tests unitarios para UpdateGlobalLayerUseCase."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.ports.config_repository import IConfigRepository, LayerName
from core.use_cases.config.update_global_layer import UpdateGlobalLayerUseCase


@pytest.fixture()
def repo() -> MagicMock:
    repo = MagicMock(spec=IConfigRepository)
    repo.read_layer.return_value = {}
    return repo


def test_escribe_en_capa_global_por_defecto(repo: MagicMock) -> None:
    """Sin pasar layer, escribe en LayerName.GLOBAL."""
    uc = UpdateGlobalLayerUseCase(repo)
    uc.execute({"llm": {"model": "nuevo-modelo"}})

    layer_escrita = repo.write_layer.call_args[0][0]
    assert layer_escrita == LayerName.GLOBAL


def test_escribe_en_capa_global_secrets(repo: MagicMock) -> None:
    """Puede escribir en GLOBAL_SECRETS si se pasa explícitamente."""
    uc = UpdateGlobalLayerUseCase(repo)
    uc.execute({"providers": {"groq": {"api_key": "k"}}}, layer=LayerName.GLOBAL_SECRETS)

    layer_escrita = repo.write_layer.call_args[0][0]
    assert layer_escrita == LayerName.GLOBAL_SECRETS


def test_merge_sobre_datos_existentes(repo: MagicMock) -> None:
    """Los cambios se mergean con los datos existentes, sin borrar campos no modificados."""
    repo.read_layer.return_value = {
        "llm": {"model": "viejo", "temperature": 0.7},
        "app": {"name": "Iñaki"},
    }
    uc = UpdateGlobalLayerUseCase(repo)
    uc.execute({"llm": {"model": "nuevo"}})

    datos_escritos = repo.write_layer.call_args[0][1]
    assert datos_escritos["llm"]["model"] == "nuevo"
    assert datos_escritos["llm"]["temperature"] == 0.7
    assert datos_escritos["app"]["name"] == "Iñaki"


def test_capa_de_agente_lanza_error(repo: MagicMock) -> None:
    """Pasar una capa de agente lanza ValueError."""
    uc = UpdateGlobalLayerUseCase(repo)
    with pytest.raises(ValueError, match="solo acepta capas globales"):
        uc.execute({}, layer=LayerName.AGENT)


def test_lee_la_capa_correcta_antes_de_escribir(repo: MagicMock) -> None:
    """Lee la capa global antes de escribir para hacer el merge."""
    uc = UpdateGlobalLayerUseCase(repo)
    uc.execute({}, layer=LayerName.GLOBAL)

    repo.read_layer.assert_called_once_with(LayerName.GLOBAL)
