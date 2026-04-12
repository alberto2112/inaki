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


# ---------------------------------------------------------------------------
# extra_sections parameter
# ---------------------------------------------------------------------------

def test_extra_sections_none_produces_same_output() -> None:
    ctx = AgentContext(agent_id="test", memory_digest="")
    result_without = ctx.build_system_prompt(BASE_PROMPT)
    result_with_none = ctx.build_system_prompt(BASE_PROMPT, extra_sections=None)
    assert result_without == result_with_none


def test_extra_sections_empty_list_produces_same_output() -> None:
    ctx = AgentContext(agent_id="test", memory_digest="")
    result_without = ctx.build_system_prompt(BASE_PROMPT)
    result_with_empty = ctx.build_system_prompt(BASE_PROMPT, extra_sections=[])
    assert result_without == result_with_empty


def test_extra_sections_single_section_appended() -> None:
    ctx = AgentContext(agent_id="test", memory_digest="")
    extra = "\n## Agentes disponibles:\n- specialist: hace cosas"
    result = ctx.build_system_prompt(BASE_PROMPT, extra_sections=[extra])
    assert result.startswith(BASE_PROMPT)
    assert extra in result


def test_extra_sections_multiple_sections_appended_in_order() -> None:
    ctx = AgentContext(agent_id="test", memory_digest="")
    section_a = "\n## Sección A"
    section_b = "\n## Sección B"
    section_c = "\n## Sección C"
    result = ctx.build_system_prompt(BASE_PROMPT, extra_sections=[section_a, section_b, section_c])
    pos_a = result.index(section_a)
    pos_b = result.index(section_b)
    pos_c = result.index(section_c)
    assert pos_a < pos_b < pos_c


def test_extra_sections_come_after_base_prompt() -> None:
    ctx = AgentContext(agent_id="test", memory_digest="")
    extra = "\n## Extra"
    result = ctx.build_system_prompt(BASE_PROMPT, extra_sections=[extra])
    assert result.index(BASE_PROMPT) < result.index(extra)


def test_extra_sections_come_after_digest_if_present() -> None:
    digest = "# Recuerdos sobre el usuario\n- entry"
    ctx = AgentContext(agent_id="test", memory_digest=digest)
    extra = "\n## Extra section"
    result = ctx.build_system_prompt(BASE_PROMPT, extra_sections=[extra])
    assert result.index(digest) < result.index(extra)


def test_extra_sections_does_not_duplicate_base_prompt() -> None:
    ctx = AgentContext(agent_id="test", memory_digest="")
    extra = "\n## Extra"
    result = ctx.build_system_prompt(BASE_PROMPT, extra_sections=[extra])
    assert result.count(BASE_PROMPT) == 1
