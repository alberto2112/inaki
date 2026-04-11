"""Tests para AgentContext.build_system_prompt — integración del digest de memoria."""

from __future__ import annotations

from core.domain.value_objects.agent_context import AgentContext


BASE_PROMPT = "Eres Iñaki, un asistente personal."


def test_empty_digest_returns_base_prompt_unchanged() -> None:
    ctx = AgentContext(agent_id="test", memory_digest="")
    result = ctx.build_system_prompt(BASE_PROMPT)
    assert result == BASE_PROMPT


def test_whitespace_only_digest_treated_as_empty() -> None:
    ctx = AgentContext(agent_id="test", memory_digest="   \n  ")
    result = ctx.build_system_prompt(BASE_PROMPT)
    assert result == BASE_PROMPT


def test_non_empty_digest_appended_verbatim() -> None:
    digest = "# Recuerdos sobre el usuario\n- [2026-04-09] Le gusta Python"
    ctx = AgentContext(agent_id="test", memory_digest=digest)
    result = ctx.build_system_prompt(BASE_PROMPT)
    assert digest in result


def test_digest_content_appears_in_prompt() -> None:
    digest = "# foo\n- bar"
    ctx = AgentContext(agent_id="test", memory_digest=digest)
    result = ctx.build_system_prompt(BASE_PROMPT)
    assert "# foo\n- bar" in result


def test_digest_not_double_wrapped_with_header() -> None:
    digest = "# Recuerdos sobre el usuario\n- entry"
    ctx = AgentContext(agent_id="test", memory_digest=digest)
    result = ctx.build_system_prompt(BASE_PROMPT)
    # The header appears exactly once, not twice
    assert result.count("# Recuerdos sobre el usuario") == 1


def test_base_prompt_always_comes_first() -> None:
    digest = "# Recuerdos sobre el usuario\n- entry"
    ctx = AgentContext(agent_id="test", memory_digest=digest)
    result = ctx.build_system_prompt(BASE_PROMPT)
    assert result.startswith(BASE_PROMPT)


def test_empty_digest_no_stray_newlines() -> None:
    ctx = AgentContext(agent_id="test", memory_digest="")
    result = ctx.build_system_prompt(BASE_PROMPT)
    assert result == BASE_PROMPT
    assert "\n\n" not in result
