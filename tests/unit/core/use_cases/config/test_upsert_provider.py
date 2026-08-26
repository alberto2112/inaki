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
    # Si se usa return_value={}, el mismo objeto dict es mutado entre lecturas
    # sucesivas, rompiendo las assertions sobre el contenido escrito.
    repo.read_layer.side_effect = lambda *args, **kwargs: {}
    return repo


def test_api_key_va_a_global(repo: MagicMock) -> None:
    """La api_key se escribe en GLOBAL, en la misma entrada que el resto."""
    uc = UpsertProviderUseCase(repo)
    uc.execute("groq", type="groq", api_key="sk-secret")

    # Ya no hay capa de secrets: la única escritura es en global.yaml
    capas_escritas = [c[0][0] for c in repo.write_layer.call_args_list]
    assert capas_escritas == [LayerName.GLOBAL]

    escritura_global = next(
        c[0][1] for c in repo.write_layer.call_args_list if c[0][0] == LayerName.GLOBAL
    )
    entrada = escritura_global["providers"]["groq"]
    assert entrada["api_key"] == "sk-secret"
    assert entrada["type"] == "groq"


def test_api_key_en_global_tiene_valor_correcto(repo: MagicMock) -> None:
    """El valor de api_key en global.yaml es el que se pasó."""
    uc = UpsertProviderUseCase(repo)
    uc.execute("openai", api_key="sk-openai-key")

    escritura_global = next(
        c[0][1] for c in repo.write_layer.call_args_list if c[0][0] == LayerName.GLOBAL
    )
    assert escritura_global["providers"]["openai"]["api_key"] == "sk-openai-key"


def test_sin_api_key_no_escribe_el_campo(repo: MagicMock) -> None:
    """Si api_key=None, la entrada no declara el campo."""
    uc = UpsertProviderUseCase(repo)
    uc.execute("ollama", type="ollama")

    escritura_global = next(
        c[0][1] for c in repo.write_layer.call_args_list if c[0][0] == LayerName.GLOBAL
    )
    assert "api_key" not in escritura_global["providers"]["ollama"]


def test_type_y_base_url_van_a_global(repo: MagicMock) -> None:
    """type y base_url se escriben en GLOBAL."""
    uc = UpsertProviderUseCase(repo)
    uc.execute("groq", type="groq", base_url="https://custom.groq.com")

    escritura_global = next(
        c[0][1] for c in repo.write_layer.call_args_list if c[0][0] == LayerName.GLOBAL
    )
    entrada = escritura_global["providers"]["groq"]
    assert entrada["type"] == "groq"
    assert entrada["base_url"] == "https://custom.groq.com"


def test_api_key_existente_se_preserva_al_actualizar_otros_campos(repo: MagicMock) -> None:
    """Actualizar type/base_url NO borra la api_key que ya vive en global.yaml."""
    import copy

    datos = {LayerName.GLOBAL: {"providers": {"groq": {"type": "groq", "api_key": "gsk_viva"}}}}
    repo.read_layer.side_effect = lambda layer, **_: copy.deepcopy(datos.get(layer, {}))

    uc = UpsertProviderUseCase(repo)
    uc.execute("groq", type="groq-v2")

    escritura_global = next(
        c[0][1] for c in repo.write_layer.call_args_list if c[0][0] == LayerName.GLOBAL
    )
    entrada = escritura_global["providers"]["groq"]
    assert entrada["api_key"] == "gsk_viva"
    assert entrada["type"] == "groq-v2"


def test_api_key_vacia_no_pisa_la_existente(repo: MagicMock) -> None:
    """``api_key=""`` equivale a no pasarla — no borra la credencial guardada."""
    import copy

    datos = {LayerName.GLOBAL: {"providers": {"groq": {"api_key": "gsk_viva"}}}}
    repo.read_layer.side_effect = lambda layer, **_: copy.deepcopy(datos.get(layer, {}))

    uc = UpsertProviderUseCase(repo)
    uc.execute("groq", api_key="")

    escritura_global = next(
        c[0][1] for c in repo.write_layer.call_args_list if c[0][0] == LayerName.GLOBAL
    )
    assert escritura_global["providers"]["groq"]["api_key"] == "gsk_viva"


def test_upsert_preserva_campos_no_modificados(repo: MagicMock) -> None:
    """Los campos existentes que no se pasan no se eliminan."""
    import copy

    datos = {
        LayerName.GLOBAL: {"providers": {"groq": {"type": "groq", "base_url": "https://old.url"}}}
    }
    repo.read_layer.side_effect = lambda layer, **_: copy.deepcopy(datos.get(layer, {}))

    uc = UpsertProviderUseCase(repo)
    # Solo actualizamos type, no base_url
    uc.execute("groq", type="groq-new")

    escritura_global = next(
        c[0][1] for c in repo.write_layer.call_args_list if c[0][0] == LayerName.GLOBAL
    )
    assert escritura_global["providers"]["groq"]["base_url"] == "https://old.url"
