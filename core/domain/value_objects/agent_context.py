from pydantic import BaseModel
from core.domain.entities.skill import Skill


class AgentContext(BaseModel):
    agent_id: str
    memory_digest: str = ""
    skills: list[Skill] = []

    def build_system_prompt(self, base_prompt: str) -> str:
        sections = [base_prompt]

        if self.memory_digest.strip():
            # El digest ya trae su propio header "# Recuerdos sobre el usuario".
            sections.append("\n" + self.memory_digest)

        if self.skills:
            skill_block = "\n".join(
                f"- **{s.name}**: {s.description}"
                + (f"\n  {s.instructions}" if s.instructions else "")
                for s in self.skills
            )
            sections.append(f"\n## Skills disponibles:\n{skill_block}")

        return "\n".join(sections)
