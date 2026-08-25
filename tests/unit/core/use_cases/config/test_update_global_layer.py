"""Tests unitarios para UpdateGlobalLayerUseCase."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.ports.config_repository import IConfigRepository, LayerName
from core.use_cases.config._merge import CampoTriestado, TristadoValor
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


def test_credenciales_van_a_la_capa_global(repo: MagicMock) -> None:
    """Las credenciales se escriben en GLOBAL — ya no hay capa de secrets."""
    uc = UpdateGlobalLayerUseCase(repo)
    uc.execute({"providers": {"groq": {"api_key": "k"}}})

    layer_escrita, datos_escritos, *_ = repo.write_layer.call_args[0]
    assert layer_escrita == LayerName.GLOBAL
    assert datos_escritos["providers"]["groq"]["api_key"] == "k"


def test_merge_sobre_datos_existentes(repo: MagicMock) -> None:
    """Los cambios se mergean con los datos existentes, sin borrar campos no modificados."""
    repo.read_layer.return_value = {
        "llm": {"model": "viejo", "temperature": 0.7},
        "app": {"name": "Inaki"},
    }
    uc = UpdateGlobalLayerUseCase(repo)
    uc.execute({"llm": {"model": "nuevo"}})

    datos_escritos = repo.write_layer.call_args[0][1]
    assert datos_escritos["llm"]["model"] == "nuevo"
    assert datos_escritos["llm"]["temperature"] == 0.7
    assert datos_escritos["app"]["name"] == "Inaki"


def test_borra_clave_con_campo_triestado_inherit(repo: MagicMock) -> None:
    """Capacidad nueva: la capa global ahora soporta borrar una clave vía
    ``CampoTriestado(INHERIT)`` (antes solo el carril de agente lo hacía)."""
    repo.read_layer.return_value = {"llm": {"model": "x", "temperature": 0.7}}
    uc = UpdateGlobalLayerUseCase(repo)
    uc.execute({"llm": {"temperature": CampoTriestado(TristadoValor.INHERIT)}})

    escritos = repo.write_layer.call_args[0][1]
    assert "temperature" not in escritos["llm"]
    assert escritos["llm"]["model"] == "x"


def test_borra_seccion_anidada_completa(repo: MagicMock) -> None:
    """Borrar una sub-sección entera poda solo esa rama, deja el resto intacto."""
    repo.read_layer.return_value = {
        "channels": {"telegram": {"token": "T", "groups": {"behavior": "autonomous"}}}
    }
    uc = UpdateGlobalLayerUseCase(repo)
    uc.execute({"channels": {"telegram": {"groups": CampoTriestado(TristadoValor.INHERIT)}}})

    telegram = repo.write_layer.call_args[0][1]["channels"]["telegram"]
    assert "groups" not in telegram
    assert telegram["token"] == "T"


def test_anade_seccion_vacia(repo: MagicMock) -> None:
    """Añadir una sección la crea como ``{}`` (defaults los aplica Pydantic al cargar)."""
    repo.read_layer.return_value = {}
    uc = UpdateGlobalLayerUseCase(repo)
    uc.execute({"channels": {"telegram": {}}})

    escritos = repo.write_layer.call_args[0][1]
    assert escritos["channels"]["telegram"] == {}


@pytest.mark.parametrize("layer", [LayerName.AGENT, LayerName.SUB_AGENT])
def test_capa_de_agente_lanza_error(repo: MagicMock, layer: LayerName) -> None:
    """GLOBAL es la única capa aceptada: cualquier otra lanza ValueError."""
    uc = UpdateGlobalLayerUseCase(repo)
    with pytest.raises(ValueError, match="solo acepta la capa global"):
        uc.execute({}, layer=layer)

    repo.write_layer.assert_not_called()


def test_lee_la_capa_correcta_antes_de_escribir(repo: MagicMock) -> None:
    """Lee la capa global antes de escribir para hacer el merge."""
    uc = UpdateGlobalLayerUseCase(repo)
    uc.execute({}, layer=LayerName.GLOBAL)

    repo.read_layer.assert_called_once_with(LayerName.GLOBAL)
