"""Tests de ``EmbeddingProviderFactory``: el provider inexistente es error del operador."""

from __future__ import annotations

import pytest

from inaki.config import EmbeddingConfig, ProviderConfig
from inaki.embedding.base import BaseEmbeddingProvider, ResolvedEmbeddingConfig
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


# ---------------------------------------------------------------------------
# verificar(embedding_cfg, providers) — la primera mitad de create, sin construir
# ---------------------------------------------------------------------------


class _EmbeddingQueCuentaConstrucciones(BaseEmbeddingProvider):
    """Fake con creds que cuenta cuántas veces se lo instancia."""

    REQUIRES_CREDENTIALS = True
    construcciones = 0

    def __init__(self, cfg: ResolvedEmbeddingConfig) -> None:
        type(self).construcciones += 1
        super().__init__(cfg)

    async def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError

    async def embed_passage(self, text: str) -> list[float]:
        raise NotImplementedError


class _EmbeddingLocal(_EmbeddingQueCuentaConstrucciones):
    REQUIRES_CREDENTIALS = False


@pytest.fixture
def _registry(monkeypatch: pytest.MonkeyPatch) -> type[_EmbeddingQueCuentaConstrucciones]:
    _EmbeddingQueCuentaConstrucciones.construcciones = 0
    monkeypatch.setattr(
        EmbeddingProviderFactory,
        "_registry",
        {"cuenta": _EmbeddingQueCuentaConstrucciones, "local": _EmbeddingLocal},
    )
    monkeypatch.setattr(EmbeddingProviderFactory, "_load", classmethod(lambda cls: None))
    return _EmbeddingQueCuentaConstrucciones


def test_verificar_acepta_una_config_completa_y_devuelve_el_adapter_sin_instanciarlo(
    _registry: type[_EmbeddingQueCuentaConstrucciones],
) -> None:
    cfg = EmbeddingConfig(provider="cuenta")

    adapter = EmbeddingProviderFactory.verificar(cfg, {"cuenta": ProviderConfig(api_key="K")})

    assert adapter is _EmbeddingQueCuentaConstrucciones
    assert _registry.construcciones == 0


def test_verificar_acepta_un_adapter_local_sin_entrada_en_providers(
    _registry: type[_EmbeddingQueCuentaConstrucciones],
) -> None:
    cfg = EmbeddingConfig(provider="local")

    assert EmbeddingProviderFactory.verificar(cfg, providers={}) is _EmbeddingLocal


def test_verificar_provider_desconocido_es_config_error_con_los_disponibles(
    _registry: type[_EmbeddingQueCuentaConstrucciones],
) -> None:
    cfg = EmbeddingConfig(provider="no-existe")

    with pytest.raises(ConfigError, match=r"'no-existe' no encontrado.*Disponibles.*cuenta"):
        EmbeddingProviderFactory.verificar(cfg, providers={})


def test_verificar_sin_entrada_y_el_adapter_exige_creds_falla(
    _registry: type[_EmbeddingQueCuentaConstrucciones],
) -> None:
    cfg = EmbeddingConfig(provider="cuenta")

    with pytest.raises(ConfigError, match="providers.cuenta"):
        EmbeddingProviderFactory.verificar(cfg, providers={})

    assert _registry.construcciones == 0


def test_verificar_resuelve_el_type_override_como_lo_hace_create(
    _registry: type[_EmbeddingQueCuentaConstrucciones],
) -> None:
    cfg = EmbeddingConfig(provider="cuenta-work")
    providers = {"cuenta-work": ProviderConfig(type="cuenta", api_key="K2")}

    assert EmbeddingProviderFactory.verificar(cfg, providers) is _EmbeddingQueCuentaConstrucciones


def test_create_pasa_por_verificar_y_despues_instancia(
    _registry: type[_EmbeddingQueCuentaConstrucciones], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un solo camino: lo que ``config_set`` verifica es lo que el arranque va a exigir."""
    llamadas: list[tuple[EmbeddingConfig, dict]] = []
    original = EmbeddingProviderFactory.verificar

    def espia(embedding_cfg: EmbeddingConfig, providers: dict[str, ProviderConfig]):
        llamadas.append((embedding_cfg, providers))
        return original(embedding_cfg, providers)

    monkeypatch.setattr(EmbeddingProviderFactory, "verificar", staticmethod(espia))
    cfg = EmbeddingConfig(provider="cuenta")
    providers = {"cuenta": ProviderConfig(api_key="K")}

    provider = EmbeddingProviderFactory.create(cfg, providers)

    assert llamadas == [(cfg, providers)]
    assert isinstance(provider, _EmbeddingQueCuentaConstrucciones)
    assert _registry.construcciones == 1


def test_create_de_un_adapter_local_sin_entrada_conserva_su_resolved_por_defecto(
    _registry: type[_EmbeddingQueCuentaConstrucciones],
) -> None:
    cfg = EmbeddingConfig(provider="local")

    provider = EmbeddingProviderFactory.create(cfg, providers={})

    assert isinstance(provider, _EmbeddingLocal)
    assert provider._cfg.provider == "local"
    assert provider._cfg.api_key is None
    assert provider._cfg.model_dirname == cfg.model_dirname


def test_create_con_config_invalida_falla_antes_de_instanciar(
    _registry: type[_EmbeddingQueCuentaConstrucciones],
) -> None:
    cfg = EmbeddingConfig(provider="cuenta")

    with pytest.raises(ConfigError, match="providers.cuenta"):
        EmbeddingProviderFactory.create(cfg, providers={})

    assert _registry.construcciones == 0
