from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel

from core.domain.entities.skill import Skill

_VAR_RE = re.compile(
    r"\{\{(WORKSPACE|TIMEZONE|DATETIME|DATE|TIME|WEEKDAY_NUMBER|WEEKDAY)(?:\[([A-Z]{2})\])?\}\}",
    re.IGNORECASE,
)

_WEEKDAY_NAMES: dict[str, list[str]] = {
    "EN": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "ES": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
    "FR": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
}


def _resolve_vars(text: str, tz_name: str | None, workspace_root: str | None) -> str:
    """Reemplaza variables dinámicas en el prompt con valores reales en runtime."""
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
        flag = (m.group(2) or "").upper()
        if token == "WORKSPACE":
            return workspace_root if workspace_root else m.group(0)
        if token == "TIMEZONE":
            return tz_display
        if token == "DATETIME":
            return now.strftime("%Y-%m-%d %H:%M")
        if token == "DATE":
            return now.strftime("%Y-%m-%d")
        if token == "TIME":
            return now.strftime("%H:%M")
        if token == "WEEKDAY":
            if flag in _WEEKDAY_NAMES:
                return _WEEKDAY_NAMES[flag][now.weekday()]
            return now.strftime("%A")  # locale del sistema
        if token == "WEEKDAY_NUMBER":
            return str(now.isoweekday())  # ISO 8601: 1=lunes, 7=domingo
        return m.group(0)

    return _VAR_RE.sub(_replace, text)


class AgentContext(BaseModel):
    agent_id: str
    user_context: str = ""
    memory_digest: str = ""
    skills: list[Skill] = []
    timezone: str | None = None
    # Raíz absoluta del workspace (misma resolución que las tools de FS). None → {{WORKSPACE}} intacto.
    workspace_root: str | None = None

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

        return _resolve_vars("\n".join(sections), self.timezone, self.workspace_root)
