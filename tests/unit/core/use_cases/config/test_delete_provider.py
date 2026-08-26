"""Tests unitarios para DeleteProviderUseCase."""

from __future__ import annotations

from unittest.mock import MagicMock


from core.ports.config_repository import IConfigRepository, LayerName
from core.use_cases.config.delete_provider import DeleteProviderUseCase


def _repo_con_providers(global_providers: dict) -> MagicMock:
    repo = MagicMock(spec=IConfigRepository)

    def read_layer(layer: LayerName, agent_id: str | None = None) -> dict:
        if layer == LayerName.GLOBAL:
            return {"providers": dict(global_providers)}
        return {}

    repo.read_layer.side_effect = read_layer
    return repo


def test_elimina_provider_de_global() -> None:
    """El provider se elimina de global.yaml."""
    repo = _repo_con_providers(
        {"groq": {"type": "groq"}, "openai": {"type": "openai"}},
    )
    uc = DeleteProviderUseCase(repo)
    uc.execute("groq")

    escritura_global = next(
        c[0][1] for c in repo.write_layer.call_args_list if c[0][0] == LayerName.GLOBAL
    )
    assert "groq" not in escritura_global["providers"]
    assert "openai" in escritura_global["providers"]


def test_elimina_la_entrada_entera_incluida_la_api_key() -> None:
    """Borrar un provider se lleva su credencial: viven en la misma entrada."""
    repo = _repo_con_providers(
        {"groq": {"type": "groq", "api_key": "gsk_secret"}},
    )
    uc = DeleteProviderUseCase(repo)
    uc.execute("groq")

    escritura_global = next(
        c[0][1] for c in repo.write_layer.call_args_list if c[0][0] == LayerName.GLOBAL
    )
    assert "groq" not in escritura_global["providers"]
    # La credencial no queda huérfana en ninguna parte del YAML escrito
    assert "gsk_secret" not in repr(escritura_global)


def test_no_toca_las_credenciales_de_otros_providers() -> None:
    """El borrado es quirúrgico: la api_key de los otros providers sobrevive."""
    repo = _repo_con_providers(
        {"groq": {"type": "groq", "api_key": "sk"}, "openai": {"api_key": "ok"}},
    )
    uc = DeleteProviderUseCase(repo)
    uc.execute("groq")

    escritura_global = next(
        c[0][1] for c in repo.write_layer.call_args_list if c[0][0] == LayerName.GLOBAL
    )
    assert "groq" not in escritura_global["providers"]
    assert escritura_global["providers"]["openai"]["api_key"] == "ok"


def test_solo_escribe_la_capa_global() -> None:
    """No hay capa de secrets que tocar: la única escritura es en global.yaml."""
    repo = _repo_con_providers({"groq": {"type": "groq", "api_key": "sk"}})
    uc = DeleteProviderUseCase(repo)
    uc.execute("groq")

    capas_escritas = [c[0][0] for c in repo.write_layer.call_args_list]
    assert capas_escritas == [LayerName.GLOBAL]


def test_provider_inexistente_es_noop() -> None:
    """Si el provider no existe, no lanza error."""
    repo = _repo_con_providers({})
    uc = DeleteProviderUseCase(repo)
    uc.execute("no-existe")  # No debe lanzar excepción

    repo.write_layer.assert_called()
