"""Fixtures de los tests de camino dorado (red de seguridad del refactor modular).

Principio: piezas REALES en todo lo que el refactor va a mover (loader de config,
``AppContainer``, use cases, SQLite de historial/memoria/scheduler, scope registry),
y fakes SOLO en los bordes externos que no dependen de nosotros: el LLM, el
embedder (modelo ONNX) y la API de Telegram.

Un fake no es un mock: tiene comportamiento determinista y cumple el port. Por eso
``FakeLLM`` devuelve un ``LLMResponse`` de verdad y ``FakeEmbedder`` produce vectores
de 384 dimensiones estables por contenido — el routing semántico corre de verdad.
"""

from __future__ import annotations

import hashlib
import textwrap
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from core.domain.value_objects.llm_response import LLMResponse
from core.ports.outbound.embedding_port import IEmbeddingProvider
from core.ports.outbound.llm_port import ILLMProvider
from inaki.shared.message import Message

# Nombre que ``resolve_provider_name`` lee del módulo del embedder (clave del cache).
PROVIDER_NAME = "fake-golden"

AGENT_ID = "asistente"
USER_ID = "42"
TELEGRAM_TOKEN = "123456:fake-token-golden"
RESPUESTA_LLM = "Hola, soy la respuesta del LLM falso."


class FakeLLM(ILLMProvider):
    """Devuelve siempre la misma respuesta de texto y registra lo que le pidieron."""

    def __init__(self, respuesta: str = RESPUESTA_LLM) -> None:
        self.respuesta = respuesta
        self.llamadas: list[dict] = []

    async def complete(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        self.llamadas.append(
            {"messages": list(messages), "system_prompt": system_prompt, "tools": tools}
        )
        return LLMResponse.of_text(self.respuesta)

    async def stream(self, messages: list[Message], system_prompt: str) -> AsyncIterator[str]:
        yield self.respuesta


class FakeEmbedder(IEmbeddingProvider):
    """Vector determinista de 384 floats derivado del hash del texto."""

    dimension = 384

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    async def embed_passage(self, text: str) -> list[float]:
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # 32 bytes → 384 floats repitiendo el digest; normalizado a [-1, 1].
        raw = (digest * 12)[: self.dimension]
        return [(b - 128) / 128 for b in raw]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Home de instancia aislado: config + data + users bajo ``tmp_path``."""
    from inaki.config.home import set_inaki_home

    home = tmp_path / "inaki_home"
    home.mkdir()
    monkeypatch.setenv("INAKI_HOME", str(home))
    set_inaki_home(home)
    try:
        yield home
    finally:
        set_inaki_home(None)


@pytest.fixture
def config_files(home: Path) -> tuple[Path, Path]:
    """Escribe un ``global.yaml`` y un agente mínimos, como los escribiría un operador.

    Sin ``ext_dirs``: el default es ``<home>/ext``, que en el home temporal no
    existe, así que no entra ninguna extensión real de esta máquina.
    """
    config_dir = home / "config"
    agents_dir = home / "agents"
    config_dir.mkdir()
    agents_dir.mkdir()
    (config_dir / "global.yaml").write_text(
        textwrap.dedent(
            """\
            app:
              log_level: WARNING
            llm:
              provider: openrouter
              model: modelo-falso
            providers:
              openrouter:
                api_key: clave-falsa
            """
        ),
        encoding="utf-8",
    )
    (agents_dir / f"{AGENT_ID}.yaml").write_text(
        textwrap.dedent(
            f"""\
            id: {AGENT_ID}
            name: Asistente
            description: agente del camino dorado
            system_prompt: "Sos un asistente de prueba. Respondé en español. Hablás con {{{{CHANNEL.SENDER}}}}."
            channels:
              telegram:
                token: "{TELEGRAM_TOKEN}"
                allowed_user_ids: ["{USER_ID}"]
                voice_enabled: false
            """
        ),
        encoding="utf-8",
    )
    return config_dir, agents_dir


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def bordes_externos(monkeypatch: pytest.MonkeyPatch, fake_llm: FakeLLM) -> FakeLLM:
    """Sustituye las factories de LLM y embedding por los fakes.

    Se parchea en el namespace de ``container`` porque es el único sitio que las
    invoca; cuando el wiring se disuelva por módulo (fase 9) este fixture cambia
    de target, no de idea.
    """
    from infrastructure import container as container_module

    monkeypatch.setattr(
        container_module.LLMProviderFactory, "create", lambda *args, **kwargs: fake_llm
    )
    monkeypatch.setattr(
        container_module.EmbeddingProviderFactory,
        "create",
        lambda *args, **kwargs: FakeEmbedder(),
    )
    return fake_llm


@pytest.fixture
def app_container(config_files: tuple[Path, Path], bordes_externos: FakeLLM):
    """El composition root REAL: mismo camino que ``inaki daemon``."""
    from inaki.config import AgentRegistry, ensure_user_config, load_global_config
    from infrastructure.container import AppContainer

    config_dir, agents_dir = config_files
    ensure_user_config(config_dir, agents_dir)
    global_config, global_raw = load_global_config(config_dir)
    registry = AgentRegistry(agents_dir, global_raw)
    return AppContainer(global_config, registry, config_dir=config_dir)
