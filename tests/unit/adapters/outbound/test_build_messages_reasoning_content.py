"""Re-inyección de ``reasoning_content`` en ``_build_messages``.

DeepSeek (y otros providers con thinking mode) requieren que el
``reasoning_content`` viaje en el assistant message del próximo payload
SOLO cuando dicho mensaje tuvo tool_calls — caso intra tool loop.

El campo ``Message.thinking`` es transitorio (vive solo en working_messages
del tool loop, nunca se persiste). Cuando está set, ``_build_messages`` lo
serializa como ``reasoning_content`` al lado de ``content`` y ``tool_calls``.
"""

from __future__ import annotations

from adapters.outbound.providers.deepseek import DeepSeekProvider
from core.domain.entities.message import Message, Role
from infrastructure.config import ResolvedLLMConfig


def _cfg() -> ResolvedLLMConfig:
    return ResolvedLLMConfig(
        provider="deepseek",
        model="deepseek-v4-pro",
        temperature=0.7,
        max_tokens=1024,
        api_key="sk-test",
    )


TOOL_CALLS = [
    {
        "id": "call_x",
        "type": "function",
        "function": {"name": "f", "arguments": "{}"},
    }
]


def test_assistant_with_tool_calls_and_thinking_emits_reasoning_content() -> None:
    provider = DeepSeekProvider(_cfg())
    msgs = [
        Message(role=Role.USER, content="hola"),
        Message(
            role=Role.ASSISTANT,
            content="ya voy",
            tool_calls=TOOL_CALLS,
            thinking="Estoy razonando paso a paso...",
        ),
        Message(role=Role.TOOL, content="ok", tool_call_id="call_x"),
    ]

    result = provider._build_messages(msgs, "sys")

    asst_dict = next(m for m in result if m.get("role") == "assistant")
    assert asst_dict["tool_calls"] == TOOL_CALLS
    assert asst_dict["reasoning_content"] == "Estoy razonando paso a paso..."


def test_assistant_with_tool_calls_without_thinking_omits_reasoning_content() -> None:
    """Sin thinking → la clave ``reasoning_content`` NO debe aparecer (no string vacío)."""
    provider = DeepSeekProvider(_cfg())
    msgs = [
        Message(role=Role.USER, content="hola"),
        Message(role=Role.ASSISTANT, content="ya voy", tool_calls=TOOL_CALLS),
        Message(role=Role.TOOL, content="ok", tool_call_id="call_x"),
    ]

    result = provider._build_messages(msgs, "sys")

    asst_dict = next(m for m in result if m.get("role") == "assistant")
    assert "reasoning_content" not in asst_dict


def test_assistant_text_only_does_not_get_reasoning_content_even_if_thinking_set() -> None:
    """Branch sin tool_calls cae en el ``elif role in (USER, ASSISTANT)`` que NO
    serializa thinking. Esto refleja la regla de la doc: sin tool calls, el
    reasoning no participa de la concatenación de contexto."""
    provider = DeepSeekProvider(_cfg())
    msgs = [
        Message(role=Role.USER, content="hola"),
        Message(role=Role.ASSISTANT, content="hola, ¿cómo estás?", thinking="ignorame"),
    ]

    result = provider._build_messages(msgs, "sys")

    asst_dict = next(m for m in result if m.get("role") == "assistant")
    assert "reasoning_content" not in asst_dict
    assert asst_dict["content"] == "hola, ¿cómo estás?"
