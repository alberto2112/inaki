from pydantic import BaseModel


class Skill(BaseModel):
    id: str
    name: str
    description: str
    instructions: str = ""            # Instrucciones detalladas para el LLM
    tags: list[str] = []


class SkillResult(BaseModel):
    skill_id: str
    applied: bool
    notes: str = ""
