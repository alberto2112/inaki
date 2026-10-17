"""``verificar_construccion``: lo que ``config_set`` exige de los adapters antes de aceptar.

El composition root la arma con ``verificar()`` de las factories de LLM y embedding (la
primera mitad de ``create``): resuelve adapter y credenciales de CADA agente —el LLM
del agente, el LLM de memoria si es distinto y el embedding— sin instanciar nada.
"""

from __future__ import annotations

import pytest

from inaki.app.assembly import verificar_construccion
from inaki.config import EmbeddingConfig, LLMConfig, ProviderConfig
from inaki.config.schema.memories import MemoryLLMConfig
from inaki.embedding.wiring import EmbeddingProviderFactory
from inaki.llm.wiring import LLMProviderFactory
from inaki.shared.errors import ConfigError
from tests.app.conftest import agent_cfg, global_cfg


@pytest.fixture(autouse=True)
def nada_se_instancia(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verificar NO es construir: si algo llama a ``create`` el test explota."""

    def _no(*_a, **_k):
        raise AssertionError("verificar_construccion no puede instanciar un provider")

    monkeypatch.setattr(LLMProviderFactory, "create", _no)
    monkeypatch.setattr(EmbeddingProviderFactory, "create", _no)


def test_una_config_sana_pasa_sin_instanciar_nada() -> None:
    verificar_construccion(global_cfg(), [agent_cfg("a"), agent_cfg("b")])


def test_un_llm_con_provider_inexistente_se_rechaza() -> None:
    roto = agent_cfg("a")
    roto.llm = LLMConfig(provider="no-existe", model="m")

    with pytest.raises(ConfigError, match="no-existe"):
        verificar_construccion(global_cfg(), [agent_cfg("sano"), roto])


def test_una_credencial_faltante_se_rechaza() -> None:
    sin_key = agent_cfg("a")
    sin_key.providers = {}  # openrouter exige api_key y ya no hay entrada

    with pytest.raises(ConfigError, match="openrouter"):
        verificar_construccion(global_cfg(), [sin_key])


def test_el_embedding_con_provider_inexistente_se_rechaza() -> None:
    roto = agent_cfg("a")
    roto.embedding = EmbeddingConfig(provider="no-existe", model_dirname="models/test")

    with pytest.raises(ConfigError, match="no-existe"):
        verificar_construccion(global_cfg(), [roto])


def test_el_llm_de_memoria_distinto_tambien_se_verifica() -> None:
    con_memoria = agent_cfg("a")
    con_memoria.memories.llm = MemoryLLMConfig(provider="memoria-inexistente")

    with pytest.raises(ConfigError, match="memoria-inexistente"):
        verificar_construccion(global_cfg(), [con_memoria])


def test_el_llm_de_memoria_con_credencial_en_providers_pasa() -> None:
    otro = agent_cfg("a")
    otro.providers = {
        "openrouter": ProviderConfig(api_key="k1"),
        "groq": ProviderConfig(api_key="k2"),
    }
    otro.memories.llm = MemoryLLMConfig(provider="groq", model="llama")

    verificar_construccion(global_cfg(), [otro])
