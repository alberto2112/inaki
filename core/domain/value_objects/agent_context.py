from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel

from core.domain.entities.skill import Skill

_VAR_RE = re.compile(r"\{\{(TIMEZONE|DATETIME|DATE|TIME)\}\}", re.IGNORECASE)


def _resolve_vars(text: str, tz_name: str | None) -> str:
    """Reemplaza {{TIMEZONE}}, {{DATETIME}}, {{DATE}} y {{TIME}} con valores reales."""
    if not _VAR_RE.search(text):
        return text

    if tz_name:
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, KeyError):
            tz = datetime.now().astimezone().tzinfo
    else:
        tz = datetime.now().astimezone().tzinfo

    now = datetime.now(tz)
    tz_display = tz_name if tz_name else now.strftime("%Z")

    def _replace(m: re.Match) -> str:
        token = m.group(1).upper()
        if token == "TIMEZONE":
            return tz_display
        if token == "DATETIME":
            return now.strftime("%Y-%m-%d %H:%M")
        if token == "DATE":
            return now.strftime("%Y-%m-%d")
        if token == "TIME":
            return now.strftime("%H:%M")
        return m.group(0)

    return _VAR_RE.sub(_replace, text)


class AgentContext(BaseModel):
    agent_id: str
    user_context: str = ""
    memory_digest: str = ""
    skills: list[Skill] = []
    timezone: str | None = None

    def build_system_prompt(
        self,
        base_prompt: str,
        extra_sections: list[str] | None = None,
    ) -> str:
        sections = [base_prompt]

        if self.user_context.strip():
            sections.append("\n" + self.user_context)

        if self.memory_digest.strip():
            # El digest ya trae su propio header "# Recuerdos sobre el usuario".
            sections.append("\n" + self.memory_digest)

        if self.skills:
            skill_blocks = []
            for s in self.skills:
                block = f"### {s.name}\n{s.description}"
                if s.instructions:
                    block += f"\n\n{s.instructions}"
                skill_blocks.append(block)
            sections.append("\n## Skills disponibles:\n\n" + "\n\n".join(skill_blocks))

        if extra_sections:
            for section in extra_sections:
                sections.append(section)

        return _resolve_vars("\n".join(sections), self.timezone)
