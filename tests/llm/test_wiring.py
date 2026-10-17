"""Tests de LLMProviderFactory — ``create(llm_cfg, providers)``.

``create`` es el ÚNICO camino de construcción: resuelve el adapter por el
``type`` de la entrada (o la key) y las creds desde el registry según
``llm_cfg.provider``. Lo usan el LLM del agente, los sub-agentes y
``memories.llm``. El segundo camino (``create_from_resolved``) se retiró
porque perdía el ``type``.

La API vieja ``create_from_llm_config(cfg)`` fue removida — las creds ya no
viven en ``LLMConfig``.
"""

from __future__ import annotations

import pytest

from inaki.llm.base import BaseLLMProvider, ResolvedLLMConfig
from inaki.config import (
    LLMConfig,
    ProviderConfig,
)
from inaki.shared.errors import ConfigError
from inaki.llm.wiring import LLMProviderFactory


class _FakeProvider(BaseLLMProvider):
    """Provider fake que exige creds — default de ``BaseLLMProvider``."""

    REQUIRES_CREDENTIALS = True

    def __init__(self, cfg: ResolvedLLMConfig) -> None:
        self.cfg = cfg

    async def complete(self, messages, system_prompt, tools=None):  # type: ignore[override]
        raise NotImplementedError

    async def stream(self, messages, system_prompt):  # type: ignore[override]
        raise NotImplementedError
        yield  # pragma: no cover


class _FakeLocalProvider(BaseLLMProvider):
    """Provider fake que NO exige creds (modelo local)."""

    REQUIRES_CREDENTIALS = False

    def __init__(self, cfg: ResolvedLLMConfig) -> None:
        self.cfg = cfg

    async def complete(self, messages, system_prompt, tools=None):  # type: ignore[override]
        raise NotImplementedError

    async def stream(self, messages, system_prompt):  # type: ignore[override]
        raise NotImplementedError
        yield  # pragma: no cover


@pytest.fixture(autouse=True)
def _reset_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aísla cada test: registry controlado, sin reescanear módulos."""
    monkeypatch.setattr(
        LLMProviderFactory,
        "_registry",
        {"fake": _FakeProvider, "fake-local": _FakeLocalProvider},
    )
    monkeypatch.setattr(LLMProviderFactory, "_load", classmethod(lambda cls: None))


# ---------------------------------------------------------------------------
# create(llm_cfg, providers)
# ---------------------------------------------------------------------------


def test_create_resuelve_creds_del_registry() -> None:
    cfg = LLMConfig(provider="fake", model="m")
    providers = {"fake": ProviderConfig(api_key="K", base_url="https://x.y")}

    provider = LLMProviderFactory.create(cfg, providers)

    assert isinstance(provider, _FakeProvider)
    assert provider.cfg.provider == "fake"
    assert provider.cfg.model == "m"
    assert provider.cfg.api_key == "K"
    assert provider.cfg.base_url == "https://x.y"


def test_create_sin_entry_en_registry_y_provider_exige_creds_falla() -> None:
    """Provider con ``REQUIRES_CREDENTIALS=True`` y sin entrada en el registry → ConfigError."""
    cfg = LLMConfig(provider="fake", model="m")

    with pytest.raises(ConfigError) as exc_info:
        LLMProviderFactory.create(cfg, providers={})

    mensaje = str(exc_info.value)
    assert "fake" in mensaje
    assert "credenciales" in mensaje.lower() or "providers.fake" in mensaje


def test_create_sin_entry_en_registry_para_provider_local_es_ok() -> None:
    """Provider con ``REQUIRES_CREDENTIALS=False`` arranca sin entry en providers."""
    cfg = LLMConfig(provider="fake-local", model="m")

    provider = LLMProviderFactory.create(cfg, providers={})

    assert isinstance(provider, _FakeLocalProvider)
    assert provider.cfg.api_key is None


def test_create_provider_desconocido_falla() -> None:
    """Es un error del operador (``ConfigError``): así ``borde_de_config`` lo
    imprime como mensaje accionable en vez de un traceback."""
    cfg = LLMConfig(provider="no-existe", model="m")

    with pytest.raises(ConfigError) as exc_info:
        LLMProviderFactory.create(cfg, providers={})

    mensaje = str(exc_info.value)
    assert "no-existe" in mensaje
    assert "Disponibles" in mensaje
    assert "fake" in mensaje


def test_create_resuelve_type_override_desde_provider_config() -> None:
    """Una entry con ``type`` explícito permite múltiples claves del mismo adapter."""
    cfg = LLMConfig(provider="fake-work", model="m")
    providers = {"fake-work": ProviderConfig(type="fake", api_key="K2")}

    provider = LLMProviderFactory.create(cfg, providers)

    assert isinstance(provider, _FakeProvider)
    assert provider.cfg.api_key == "K2"
    # La key del registry (el provider que pedía el usuario) se preserva en el resolved.
    assert provider.cfg.provider == "fake-work"


def test_create_entry_sin_api_key_y_provider_exige_creds_falla() -> None:
    """La entrada existe pero sin ``api_key`` → ``ConfigError`` que nombra la
    entrada, no un error del adapter: así lo ataja el borde de config."""
    cfg = LLMConfig(provider="fake-work", model="m")
    providers = {"fake-work": ProviderConfig(type="fake", base_url="https://x.y")}

    with pytest.raises(ConfigError) as exc_info:
        LLMProviderFactory.create(cfg, providers)

    assert "providers.fake-work" in str(exc_info.value)
    assert "api_key" in str(exc_info.value)


def test_create_entry_sin_api_key_para_provider_local_es_ok() -> None:
    """Un adapter local (``REQUIRES_CREDENTIALS=False``) acepta una entrada que
    solo aporta ``base_url``."""
    cfg = LLMConfig(provider="fake-local", model="m")
    providers = {"fake-local": ProviderConfig(base_url="http://lan:8080/v1")}

    provider = LLMProviderFactory.create(cfg, providers)

    assert isinstance(provider, _FakeLocalProvider)
    assert provider.cfg.base_url == "http://lan:8080/v1"


# ---------------------------------------------------------------------------
# verificar(llm_cfg, providers) — la primera mitad de create, sin construir
# ---------------------------------------------------------------------------


class _ProviderQueCuentaConstrucciones(_FakeProvider):
    """Fake que cuenta cuántas veces se lo instancia: ``verificar`` no debe construir."""

    construcciones = 0

    def __init__(self, cfg: ResolvedLLMConfig) -> None:
        type(self).construcciones += 1
        super().__init__(cfg)


@pytest.fixture
def _contador(monkeypatch: pytest.MonkeyPatch) -> type[_ProviderQueCuentaConstrucciones]:
    _ProviderQueCuentaConstrucciones.construcciones = 0
    monkeypatch.setattr(
        LLMProviderFactory,
        "_registry",
        {"cuenta": _ProviderQueCuentaConstrucciones, "fake-local": _FakeLocalProvider},
    )
    return _ProviderQueCuentaConstrucciones


def test_verificar_acepta_una_config_completa_y_devuelve_el_adapter_sin_instanciarlo(
    _contador: type[_ProviderQueCuentaConstrucciones],
) -> None:
    cfg = LLMConfig(provider="cuenta", model="m")

    adapter = LLMProviderFactory.verificar(cfg, {"cuenta": ProviderConfig(api_key="K")})

    assert adapter is _ProviderQueCuentaConstrucciones
    assert _contador.construcciones == 0


def test_verificar_acepta_un_adapter_local_sin_entrada_en_providers() -> None:
    cfg = LLMConfig(provider="fake-local", model="m")

    assert LLMProviderFactory.verificar(cfg, providers={}) is _FakeLocalProvider


def test_verificar_provider_desconocido_es_config_error_con_los_disponibles() -> None:
    cfg = LLMConfig(provider="no-existe", model="m")

    with pytest.raises(ConfigError, match=r"'no-existe' no encontrado.*Disponibles.*fake"):
        LLMProviderFactory.verificar(cfg, providers={})


def test_verificar_sin_entrada_y_el_adapter_exige_creds_falla(
    _contador: type[_ProviderQueCuentaConstrucciones],
) -> None:
    cfg = LLMConfig(provider="cuenta", model="m")

    with pytest.raises(ConfigError, match="providers.cuenta"):
        LLMProviderFactory.verificar(cfg, providers={})

    assert _contador.construcciones == 0


def test_verificar_entrada_sin_api_key_y_el_adapter_exige_creds_falla(
    _contador: type[_ProviderQueCuentaConstrucciones],
) -> None:
    cfg = LLMConfig(provider="cuenta-work", model="m")
    providers = {"cuenta-work": ProviderConfig(type="cuenta", base_url="https://x.y")}

    with pytest.raises(ConfigError, match=r"providers.cuenta-work.*api_key"):
        LLMProviderFactory.verificar(cfg, providers)

    assert _contador.construcciones == 0


def test_verificar_resuelve_el_type_override_como_lo_hace_create(
    _contador: type[_ProviderQueCuentaConstrucciones],
) -> None:
    cfg = LLMConfig(provider="cuenta-work", model="m")
    providers = {"cuenta-work": ProviderConfig(type="cuenta", api_key="K2")}

    assert LLMProviderFactory.verificar(cfg, providers) is _ProviderQueCuentaConstrucciones


def test_create_pasa_por_verificar_y_despues_instancia(
    _contador: type[_ProviderQueCuentaConstrucciones], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un solo camino: lo que ``config_set`` verifica es lo que el arranque va a exigir."""
    llamadas: list[tuple[LLMConfig, dict]] = []
    original = LLMProviderFactory.verificar

    def espia(llm_cfg: LLMConfig, providers: dict[str, ProviderConfig]):
        llamadas.append((llm_cfg, providers))
        return original(llm_cfg, providers)

    monkeypatch.setattr(LLMProviderFactory, "verificar", staticmethod(espia))
    cfg = LLMConfig(provider="cuenta", model="m")
    providers = {"cuenta": ProviderConfig(api_key="K")}

    provider = LLMProviderFactory.create(cfg, providers)

    assert llamadas == [(cfg, providers)]
    assert isinstance(provider, _ProviderQueCuentaConstrucciones)
    assert _contador.construcciones == 1


def test_create_con_config_invalida_falla_en_verificar_antes_de_instanciar(
    _contador: type[_ProviderQueCuentaConstrucciones],
) -> None:
    cfg = LLMConfig(provider="cuenta", model="m")

    with pytest.raises(ConfigError, match="providers.cuenta"):
        LLMProviderFactory.create(cfg, providers={})

    assert _contador.construcciones == 0
