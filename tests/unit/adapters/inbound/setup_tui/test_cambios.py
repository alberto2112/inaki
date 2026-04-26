"""Tests del helper ``build_cambios`` (jerarquía de sub-secciones)."""

from __future__ import annotations

from adapters.inbound.setup_tui._cambios import build_cambios


_MAPPING = {
    "APP": "app",
    "MEMORY": "memory",
    "MEMORY.LLM": "memory",
    "SCHEDULER": "scheduler",
    "SCHEDULER.CHANNEL_FALLBACK": "scheduler",
}


class TestBuildCambiosSeccionPlana:
    """Sección sin punto → un solo nivel de anidación."""

    def test_app_name(self) -> None:
        result = build_cambios(
            "APP", "name", "Iñaki", section_to_yaml=_MAPPING
        )
        assert result == {"app": {"name": "Iñaki"}}

    def test_memory_default_top_k(self) -> None:
        result = build_cambios(
            "MEMORY", "default_top_k", 7, section_to_yaml=_MAPPING
        )
        assert result == {"memory": {"default_top_k": 7}}


class TestBuildCambiosSeccionAnidada:
    """Sección con punto (PADRE.HIJO) → dos niveles de anidación."""

    def test_memory_llm_provider(self) -> None:
        result = build_cambios(
            "MEMORY.LLM", "provider", "groq", section_to_yaml=_MAPPING
        )
        assert result == {"memory": {"llm": {"provider": "groq"}}}

    def test_memory_llm_value_none(self) -> None:
        """Override null se persiste correctamente como None anidado."""
        result = build_cambios(
            "MEMORY.LLM", "max_tokens", None, section_to_yaml=_MAPPING
        )
        assert result == {"memory": {"llm": {"max_tokens": None}}}

    def test_scheduler_channel_fallback(self) -> None:
        result = build_cambios(
            "SCHEDULER.CHANNEL_FALLBACK",
            "max_messages",
            42,
            section_to_yaml=_MAPPING,
        )
        assert result == {"scheduler": {"channel_fallback": {"max_messages": 42}}}


class TestBuildCambiosRootFields:
    """Los campos raíz (id, name, etc.) van planos sin contenedor."""

    def test_root_field_skip_section(self) -> None:
        result = build_cambios(
            "AGENTCONFIG",
            "id",
            "general",
            section_to_yaml={"AGENTCONFIG": "agent"},
            root_fields=frozenset({"id", "name"}),
        )
        assert result == {"id": "general"}

    def test_non_root_field_uses_section(self) -> None:
        result = build_cambios(
            "LLM",
            "model",
            "claude-haiku",
            section_to_yaml={"LLM": "llm"},
            root_fields=frozenset({"id", "name"}),
        )
        assert result == {"llm": {"model": "claude-haiku"}}


class TestBuildCambiosSeccionDesconocida:
    """Si la sección no está en el mapping, se cae al lowercase del nombre."""

    def test_unknown_section_lowercased(self) -> None:
        result = build_cambios(
            "DESCONOCIDA", "campo", "valor", section_to_yaml={}
        )
        assert result == {"desconocida": {"campo": "valor"}}
