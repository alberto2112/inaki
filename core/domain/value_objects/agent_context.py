from pydantic import BaseModel
from core.domain.entities.memory import MemoryEntry
from core.domain.entities.skill import Skill


class AgentContext(BaseModel):
    agent_id: str
    memories: list[MemoryEntry] = []
    skills: list[Skill] = []

    def build_system_prompt(self, base_prompt: str) -> str:
        """Construye el system prompt dinámico inyectando memoria y skills relevantes."""
        sections = [base_prompt]

        if self.memories:
            mem_block = "\n".join(f"- {m.content}" for m in self.memories)
            sections.append(f"\n## Lo que recuerdas del usuario:\n{mem_block}")

        if self.skills:
            skill_block = "\n".join(
                f"- **{s.name}**: {s.description}"
                + (f"\n  {s.instructions}" if s.instructions else "")
                for s in self.skills
            )
            sections.append(f"\n## Skills disponibles:\n{skill_block}")

        return "\n".join(sections)
