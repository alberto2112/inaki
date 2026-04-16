"""Contrato de ``_build_messages`` para todos los providers LLM.

Regresión: el refactor de 2026-04-16 eliminó overrides en ``OpenAIProvider`` y
``OpenRouterProvider`` que silenciosamente tiraban los mensajes con rol ``TOOL``
y ``ASSISTANT+tool_calls``, rompiendo tool-calling multi-turno. Este test
fija el contrato mínimo: los 4 providers deben preservar íntegro el protocolo
de tool calls al serializar el historial.
"""

from __future__ import annotations

import json

import pytest

from adapters.outbound.providers.groq import GroqProvider
from adapters.outbound.providers.ollama import OllamaProvider
from adapters.outbound.providers.openai import OpenAIProvider
from adapters.outbound.providers.openrouter import OpenRouterProvider
from core.domain.entities.message import Message, Role
from infrastructure.config import LLMConfig


SYSTEM_PROMPT = "Eres un asistente de test."

TOOL_CALLS = [
    {
        "id": "call_123",
        "type": "function",
        "function": {
            "name": "create_scheduled_task",
            "arguments": '{"cron":"0 9 * * *","prompt":"saluda"}',
        },
    }
]

CONVERSATION = [
    Message(role=Role.USER, content="programame una tarea"),
    Message(role=Role.ASSISTANT, content="", tool_calls=TOOL_CALLS),
    Message(role=Role.TOOL, content="Tarea creada id=abc", tool_call_id="call_123"),
    Message(role=Role.ASSISTANT, content="Listo, programada."),
]


def _make_cfg() -> LLMConfig:
    return LLMConfig(provider="x", model="m", api_key="k")


PROVIDERS_OPENAI_COMPAT = [
    ("openai", OpenAIProvider),
    ("openrouter", OpenRouterProvider),
    ("groq", GroqProvider),
]


@pytest.mark.parametrize("name,provider_cls", PROVIDERS_OPENAI_COMPAT)
def test_openai_compat_preserves_tool_protocol(name: str, provider_cls: type) -> None:
    """openai/openrouter/groq producen formato OpenAI idéntico: arguments = string JSON."""
    provider = provider_cls(_make_cfg())

    result = provider._build_messages(CONVERSATION, SYSTEM_PROMPT)

    # system + 4 mensajes del historial, ninguno perdido
    assert len(result) == 5, f"{name}: se perdieron mensajes del historial"
    assert result[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert result[1] == {"role": "user", "content": "programame una tarea"}

    # ASSISTANT con tool_calls: content=None cuando no hubo texto, tool_calls intactos
    assert result[2]["role"] == "assistant"
    assert result[2]["content"] is None
    assert result[2]["tool_calls"] == TOOL_CALLS
    # arguments debe seguir siendo string (contrato OpenAI)
    assert isinstance(result[2]["tool_calls"][0]["function"]["arguments"], str)

    # TOOL result con tool_call_id correcto
    assert result[3] == {
        "role": "tool",
        "tool_call_id": "call_123",
        "content": "Tarea creada id=abc",
    }

    # ASSISTANT final textual
    assert result[4] == {"role": "assistant", "content": "Listo, programada."}


def test_ollama_desnormaliza_arguments_a_dict() -> None:
    """Ollama hereda del base pero desnormaliza arguments string→dict (formato nativo)."""
    provider = OllamaProvider(_make_cfg())

    result = provider._build_messages(CONVERSATION, SYSTEM_PROMPT)

    # Mismo preservado de mensajes que los otros providers
    assert len(result) == 5
    assert result[2]["role"] == "assistant"
    assert result[2]["tool_calls"][0]["function"]["name"] == "create_scheduled_task"

    # Clave: arguments es dict, no string JSON
    args = result[2]["tool_calls"][0]["function"]["arguments"]
    assert isinstance(args, dict), "Ollama espera arguments como dict en el wire"
    assert args == {"cron": "0 9 * * *", "prompt": "saluda"}

    # No mutamos el Message original (el string JSON sigue siendo string en CONVERSATION)
    assert isinstance(TOOL_CALLS[0]["function"]["arguments"], str)


@pytest.mark.parametrize("name,provider_cls", PROVIDERS_OPENAI_COMPAT + [("ollama", OllamaProvider)])
def test_todos_preservan_mensajes_solo_texto(name: str, provider_cls: type) -> None:
    """Sanidad: una conversación sin tools nunca debe perder mensajes en ningún provider."""
    provider = provider_cls(_make_cfg())
    conv = [
        Message(role=Role.USER, content="hola"),
        Message(role=Role.ASSISTANT, content="qué tal"),
        Message(role=Role.USER, content="bien"),
    ]

    result = provider._build_messages(conv, SYSTEM_PROMPT)

    assert len(result) == 4  # system + 3
    assert [m["role"] for m in result] == ["system", "user", "assistant", "user"]
    assert [m["content"] for m in result] == [SYSTEM_PROMPT, "hola", "qué tal", "bien"]


def test_ollama_roundtrip_arguments_parseables() -> None:
    """Post-desnormalizado, el dict debe ser exactamente el JSON parseado del string original."""
    provider = OllamaProvider(_make_cfg())

    result = provider._build_messages(CONVERSATION, SYSTEM_PROMPT)

    wire_args = result[2]["tool_calls"][0]["function"]["arguments"]
    original_args = json.loads(TOOL_CALLS[0]["function"]["arguments"])
    assert wire_args == original_args
