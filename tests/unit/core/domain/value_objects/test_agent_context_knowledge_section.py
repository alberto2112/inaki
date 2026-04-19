"""
Tests unitarios para la sección "## Relevant Knowledge" de AgentContext.

Verifica:
- La sección se renderiza cuando knowledge_chunks no está vacía
- La sección está ausente cuando knowledge_chunks está vacía (default)
- Los fragmentos se agrupan correctamente por source_id
- El header es en inglés ("## Relevant Knowledge")
"""

from __future__ import annotations

from core.domain.value_objects.agent_context import AgentContext
from core.domain.value_objects.knowledge_chunk import KnowledgeChunk


def _make_chunk(
    source_id: str = "memory",
    content: str = "fragmento de prueba",
    score: float = 0.8,
) -> KnowledgeChunk:
    return KnowledgeChunk(source_id=source_id, content=content, score=score)


class TestKnowledgeSectionAbsent:
    """La sección no aparece cuando no hay chunks."""

    def test_sin_chunks_no_aparece_seccion(self) -> None:
        ctx = AgentContext(agent_id="agente-1")
        prompt = ctx.build_system_prompt(base_prompt="Sistema base")

        assert "## Relevant Knowledge" not in prompt

    def test_knowledge_chunks_vacio_por_defecto(self) -> None:
        ctx = AgentContext(agent_id="agente-1")
        assert ctx.knowledge_chunks == []

    def test_lista_vacia_explicita_no_renderiza(self) -> None:
        ctx = AgentContext(agent_id="agente-1", knowledge_chunks=[])
        prompt = ctx.build_system_prompt(base_prompt="Sistema")

        assert "## Relevant Knowledge" not in prompt


class TestKnowledgeSectionPresent:
    """La sección se renderiza con el formato correcto cuando hay chunks."""

    def test_un_chunk_renderiza_seccion(self) -> None:
        ctx = AgentContext(
            agent_id="agente-1",
            knowledge_chunks=[_make_chunk(content="Inaki es un asistente")],
        )
        prompt = ctx.build_system_prompt(base_prompt="Sistema")

        assert "## Relevant Knowledge" in prompt
        assert "Inaki es un asistente" in prompt

    def test_header_en_ingles(self) -> None:
        """El header debe ser exactamente '## Relevant Knowledge' (inglés)."""
        ctx = AgentContext(
            agent_id="agente-1",
            knowledge_chunks=[_make_chunk()],
        )
        prompt = ctx.build_system_prompt(base_prompt="Sistema")

        assert "## Relevant Knowledge" in prompt
        # No debe aparecer versión en español
        assert "## Conocimiento Relevante" not in prompt

    def test_score_formateado_en_seccion(self) -> None:
        ctx = AgentContext(
            agent_id="agente-1",
            knowledge_chunks=[_make_chunk(score=0.75)],
        )
        prompt = ctx.build_system_prompt(base_prompt="Sistema")

        # Score debe aparecer con 2 decimales según formato [score]
        assert "[0.75]" in prompt

    def test_contenido_del_chunk_aparece(self) -> None:
        ctx = AgentContext(
            agent_id="agente-1",
            knowledge_chunks=[_make_chunk(content="Mi contenido especial")],
        )
        prompt = ctx.build_system_prompt(base_prompt="Sistema")

        assert "Mi contenido especial" in prompt


class TestKnowledgeSectionGroupedBySourceId:
    """Los fragmentos se agrupan por source_id con sub-headers."""

    def test_un_source_id_genera_sub_header(self) -> None:
        ctx = AgentContext(
            agent_id="agente-1",
            knowledge_chunks=[
                _make_chunk(source_id="memory", content="mem 1"),
                _make_chunk(source_id="memory", content="mem 2"),
            ],
        )
        prompt = ctx.build_system_prompt(base_prompt="Sistema")

        assert "### memory" in prompt
        assert "mem 1" in prompt
        assert "mem 2" in prompt

    def test_multiples_sources_generan_sub_headers_separados(self) -> None:
        ctx = AgentContext(
            agent_id="agente-1",
            knowledge_chunks=[
                _make_chunk(source_id="memory", content="recuerdo"),
                _make_chunk(source_id="docs-proyecto", content="documentación"),
            ],
        )
        prompt = ctx.build_system_prompt(base_prompt="Sistema")

        assert "### memory" in prompt
        assert "### docs-proyecto" in prompt
        assert "recuerdo" in prompt
        assert "documentación" in prompt

    def test_orden_de_grupos_sigue_orden_de_aparicion(self) -> None:
        """El grupo que aparece primero en knowledge_chunks sale primero."""
        ctx = AgentContext(
            agent_id="agente-1",
            knowledge_chunks=[
                _make_chunk(source_id="docs", content="primero"),
                _make_chunk(source_id="memory", content="segundo"),
            ],
        )
        prompt = ctx.build_system_prompt(base_prompt="Sistema")

        pos_docs = prompt.index("### docs")
        pos_memory = prompt.index("### memory")
        assert pos_docs < pos_memory

    def test_chunks_del_mismo_source_agrupados_juntos(self) -> None:
        """Tres chunks de 'memory' deben quedar bajo el mismo ### memory."""
        ctx = AgentContext(
            agent_id="agente-1",
            knowledge_chunks=[
                _make_chunk(source_id="memory", content="A"),
                _make_chunk(source_id="memory", content="B"),
                _make_chunk(source_id="memory", content="C"),
            ],
        )
        prompt = ctx.build_system_prompt(base_prompt="Sistema")

        # Solo debe haber una ocurrencia de "### memory"
        assert prompt.count("### memory") == 1
        # Los tres contenidos deben aparecer
        assert "A" in prompt
        assert "B" in prompt
        assert "C" in prompt


class TestKnowledgeSectionPosition:
    """La sección aparece en el lugar correcto del prompt (antes de extra_sections)."""

    def test_seccion_knowledge_aparece_antes_de_extra_sections(self) -> None:
        ctx = AgentContext(
            agent_id="agente-1",
            knowledge_chunks=[_make_chunk(content="knowledge content")],
        )
        prompt = ctx.build_system_prompt(
            base_prompt="Sistema",
            extra_sections=["Extra sección final"],
        )

        pos_knowledge = prompt.index("## Relevant Knowledge")
        pos_extra = prompt.index("Extra sección final")
        assert pos_knowledge < pos_extra

    def test_seccion_knowledge_aparece_despues_de_skills(self) -> None:
        from core.domain.entities.skill import Skill

        ctx = AgentContext(
            agent_id="agente-1",
            skills=[Skill(id="s1", name="Skill1", description="desc skill")],
            knowledge_chunks=[_make_chunk(content="knowledge content")],
        )
        prompt = ctx.build_system_prompt(base_prompt="Sistema")

        pos_skills = prompt.index("## Skills disponibles:")
        pos_knowledge = prompt.index("## Relevant Knowledge")
        assert pos_skills < pos_knowledge
