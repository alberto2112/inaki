"""Tests para AgentContext.build_system_prompt — integración del digest de memoria."""

from __future__ import annotations

import pytest
from freezegun import freeze_time

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


# ---------------------------------------------------------------------------
# Variable interpolation — {{TIMEZONE}}, {{DATETIME}}, {{DATE}}, {{TIME}}
# ---------------------------------------------------------------------------

_FROZEN_UTC = "2026-04-12 15:30:00"
_FROZEN_BSAS = "2026-04-12 12:30:00"  # UTC-3


@freeze_time("2026-04-12 15:30:00", tz_offset=0)
def test_datetime_var_replaced() -> None:
    ctx = AgentContext(agent_id="test", timezone="UTC")
    result = ctx.build_system_prompt("Ahora son {{DATETIME}}.")
    assert "{{DATETIME}}" not in result
    assert "2026-04-12 15:30" in result


@freeze_time("2026-04-12 15:30:00", tz_offset=0)
def test_date_var_replaced() -> None:
    ctx = AgentContext(agent_id="test", timezone="UTC")
    result = ctx.build_system_prompt("Fecha: {{DATE}}.")
    assert "{{DATE}}" not in result
    assert "2026-04-12" in result


@freeze_time("2026-04-12 15:30:00", tz_offset=0)
def test_time_var_replaced() -> None:
    ctx = AgentContext(agent_id="test", timezone="UTC")
    result = ctx.build_system_prompt("Hora: {{TIME}}.")
    assert "{{TIME}}" not in result
    assert "15:30" in result


@freeze_time("2026-04-12 15:30:00", tz_offset=0)
def test_timezone_var_replaced_from_config() -> None:
    ctx = AgentContext(agent_id="test", timezone="America/Argentina/Buenos_Aires")
    result = ctx.build_system_prompt("TZ: {{TIMEZONE}}.")
    assert "{{TIMEZONE}}" not in result
    assert "America/Argentina/Buenos_Aires" in result


@freeze_time("2026-04-12 15:30:00", tz_offset=0)
def test_var_replacement_is_case_insensitive() -> None:
    ctx = AgentContext(agent_id="test", timezone="UTC")
    result = ctx.build_system_prompt("{{datetime}} / {{DATE}} / {{Time}}")
    assert "{{datetime}}" not in result
    assert "{{DATE}}" not in result
    assert "{{Time}}" not in result


@freeze_time("2026-04-12 15:30:00", tz_offset=0)
def test_datetime_uses_configured_timezone_offset() -> None:
    ctx_utc = AgentContext(agent_id="test", timezone="UTC")
    ctx_bsas = AgentContext(agent_id="test", timezone="America/Argentina/Buenos_Aires")
    result_utc = ctx_utc.build_system_prompt("{{DATETIME}}")
    result_bsas = ctx_bsas.build_system_prompt("{{DATETIME}}")
    # Buenos Aires es UTC-3: 15:30 UTC → 12:30 local
    assert "15:30" in result_utc
    assert "12:30" in result_bsas


def test_prompt_without_vars_unchanged() -> None:
    ctx = AgentContext(agent_id="test", timezone="UTC")
    prompt = "Sin variables aquí."
    assert ctx.build_system_prompt(prompt) == prompt


def test_unknown_var_left_as_is() -> None:
    ctx = AgentContext(agent_id="test", timezone="UTC")
    result = ctx.build_system_prompt("{{USER_NAME}} no se toca.")
    assert "{{USER_NAME}}" in result


@freeze_time("2026-04-12 15:30:00", tz_offset=0)
def test_vars_resolved_in_extra_sections() -> None:
    ctx = AgentContext(agent_id="test", timezone="UTC")
    result = ctx.build_system_prompt(BASE_PROMPT, extra_sections=["\nHora: {{TIME}}"])
    assert "{{TIME}}" not in result
    assert "15:30" in result


@freeze_time("2026-04-12 15:30:00", tz_offset=0)
def test_invalid_timezone_falls_back_gracefully() -> None:
    ctx = AgentContext(agent_id="test", timezone="Mars/Olympus_Mons")
    # No debe lanzar excepción — usa fallback a TZ local del sistema
    result = ctx.build_system_prompt("{{DATETIME}}")
    assert "{{DATETIME}}" not in result


@pytest.mark.parametrize("var", ["{{TIMEZONE}}", "{{DATETIME}}", "{{DATE}}", "{{TIME}}"])
@freeze_time("2026-04-12 15:30:00", tz_offset=0)
def test_all_vars_replaced_when_present(var: str) -> None:
    ctx = AgentContext(agent_id="test", timezone="UTC")
    result = ctx.build_system_prompt(var)
    assert var not in result


# ---------------------------------------------------------------------------
# {{WEEKDAY}} y {{WEEKDAY_NUMBER}} — 2026-04-12 es domingo (isoweekday=7)
# ---------------------------------------------------------------------------

@freeze_time("2026-04-12 15:30:00", tz_offset=0)
def test_weekday_en_flag() -> None:
    ctx = AgentContext(agent_id="test", timezone="UTC")
    result = ctx.build_system_prompt("{{WEEKDAY[EN]}}")
    assert result == "Sunday"


@freeze_time("2026-04-12 15:30:00", tz_offset=0)
def test_weekday_es_flag() -> None:
    ctx = AgentContext(agent_id="test", timezone="UTC")
    result = ctx.build_system_prompt("{{WEEKDAY[ES]}}")
    assert result == "domingo"


@freeze_time("2026-04-12 15:30:00", tz_offset=0)
def test_weekday_fr_flag() -> None:
    ctx = AgentContext(agent_id="test", timezone="UTC")
    result = ctx.build_system_prompt("{{WEEKDAY[FR]}}")
    assert result == "dimanche"


@freeze_time("2026-04-12 15:30:00", tz_offset=0)
def test_weekday_number_is_iso_8601() -> None:
    # ISO 8601: 1=lunes … 7=domingo
    ctx = AgentContext(agent_id="test", timezone="UTC")
    result = ctx.build_system_prompt("{{WEEKDAY_NUMBER}}")
    assert result == "7"


@freeze_time("2026-04-12 15:30:00", tz_offset=0)
def test_weekday_flag_case_insensitive() -> None:
    ctx = AgentContext(agent_id="test", timezone="UTC")
    assert ctx.build_system_prompt("{{weekday[en]}}") == "Sunday"
    assert ctx.build_system_prompt("{{WEEKDAY[es]}}") == "domingo"
    assert ctx.build_system_prompt("{{Weekday[Fr]}}") == "dimanche"


@freeze_time("2026-04-12 15:30:00", tz_offset=0)
def test_weekday_unknown_flag_falls_back_to_locale() -> None:
    ctx = AgentContext(agent_id="test", timezone="UTC")
    result = ctx.build_system_prompt("{{WEEKDAY[XX]}}")
    # No debe lanzar excepción y no debe quedar el placeholder
    assert "{{WEEKDAY[XX]}}" not in result


@freeze_time("2026-04-12 15:30:00", tz_offset=0)
def test_weekday_and_weekday_number_in_same_prompt() -> None:
    ctx = AgentContext(agent_id="test", timezone="UTC")
    result = ctx.build_system_prompt("{{WEEKDAY[ES]}} (día {{WEEKDAY_NUMBER}})")
    assert result == "domingo (día 7)"


@pytest.mark.parametrize("var", ["{{WEEKDAY[EN]}}", "{{WEEKDAY[ES]}}", "{{WEEKDAY[FR]}}", "{{WEEKDAY_NUMBER}}"])
@freeze_time("2026-04-12 15:30:00", tz_offset=0)
def test_new_vars_all_replaced(var: str) -> None:
    ctx = AgentContext(agent_id="test", timezone="UTC")
    result = ctx.build_system_prompt(var)
    assert var not in result
