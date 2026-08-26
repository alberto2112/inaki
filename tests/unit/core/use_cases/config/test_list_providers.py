"""Tests unitarios para ListProvidersUseCase."""

from __future__ import annotations

from unittest.mock import MagicMock


from core.ports.config_repository import IConfigRepository, LayerName
from core.use_cases.config.list_providers import ListProvidersUseCase, ProviderInfo


def _repo_con_datos(global_data: dict) -> MagicMock:
    repo = MagicMock(spec=IConfigRepository)

    def read_layer(layer: LayerName, agent_id: str | None = None) -> dict:
        if layer == LayerName.GLOBAL:
            return global_data
        return {}

    repo.read_layer.side_effect = read_layer
    return repo


def test_lista_providers_sin_api_key() -> None:
    """Los providers se devuelven SIN el valor de la api_key.

    La credencial vive en la misma entrada de ``global.yaml`` que el resto de
    los campos, así que el filtrado es responsabilidad del use case.
    """
    repo = _repo_con_datos(
        global_data={
            "providers": {
                "groq": {
                    "type": "groq",
                    "base_url": "https://api.groq.com",
                    "api_key": "gsk_secret",
                }
            }
        },
    )
    uc = ListProvidersUseCase(repo)
    resultado = uc.execute()

    assert len(resultado) == 1
    groq = resultado[0]
    assert isinstance(groq, ProviderInfo)
    assert groq.key == "groq"
    assert groq.type == "groq"
    assert groq.base_url == "https://api.groq.com"
    # ProviderInfo no expone la credencial, solo si está definida
    assert not hasattr(groq, "api_key")
    assert groq.tiene_api_key is True


def test_tiene_api_key_true_cuando_existe_en_global() -> None:
    """tiene_api_key=True si la api_key está en la entrada de global.yaml."""
    repo = _repo_con_datos(
        global_data={"providers": {"openai": {"type": "openai", "api_key": "sk-xxx"}}},
    )
    uc = ListProvidersUseCase(repo)
    resultado = uc.execute()

    assert resultado[0].tiene_api_key is True


def test_tiene_api_key_false_cuando_no_existe() -> None:
    """tiene_api_key=False si la entrada no declara api_key."""
    repo = _repo_con_datos(
        global_data={"providers": {"ollama": {"type": "ollama"}}},
    )
    uc = ListProvidersUseCase(repo)
    resultado = uc.execute()

    assert resultado[0].tiene_api_key is False


def test_lista_vacia_sin_exception() -> None:
    """Sin providers devuelve lista vacía sin error."""
    repo = _repo_con_datos({})
    uc = ListProvidersUseCase(repo)
    assert uc.execute() == []


def test_lista_todos_los_providers_de_global() -> None:
    """Todos los providers del registry aparecen, con o sin credencial."""
    repo = _repo_con_datos(
        global_data={
            "providers": {
                "groq": {"type": "groq"},
                "openai": {"api_key": "sk"},
            }
        },
    )
    uc = ListProvidersUseCase(repo)
    resultado = uc.execute()

    keys = {p.key for p in resultado}
    assert keys == {"groq", "openai"}


def test_solo_lee_la_capa_global() -> None:
    """El registry vive entero en global.yaml — no hay otra capa que leer."""
    repo = _repo_con_datos({"providers": {"groq": {"type": "groq"}}})
    uc = ListProvidersUseCase(repo)
    uc.execute()

    capas_leidas = [call[0][0] for call in repo.read_layer.call_args_list]
    assert capas_leidas == [LayerName.GLOBAL]


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
    )
    uc = ListProvidersUseCase(repo)
    resultado = uc.execute()

    assert [p.key for p in resultado] == ["aa-provider", "mm-provider", "zz-provider"]
