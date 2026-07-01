from __future__ import annotations

import re
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel

from core.domain.entities.skill import Skill
from core.domain.value_objects.knowledge_chunk import KnowledgeChunk

_VAR_RE = re.compile(
    r"\{\{("
    r"WORKSPACE|TIMEZONE|DATETIME|DATE|TIME|WEEKDAY_NUMBER|WEEKDAY"
    # Las variantes específicas (CHANNEL.SENDER, CHANNEL.USERNAME, etc.) deben ir
    # ANTES de CHANNEL "pelado" en la alternancia: regex usa first-match wins y
    # CHANNEL solo capturaría "CHANNEL" dejando ".SENDER" colgando fuera del token.
    r"|CHANNEL\.NAME|CHANNEL\.CHATID|CHANNEL\.CONTEXTID"
    r"|CHANNEL\.SENDER|CHANNEL\.USERNAME|CHANNEL\.FIRST_NAME|CHANNEL\.LAST_NAME"
    r"|CHANNEL"
    r")(?:\[([A-Z]{2})\])?\}\}",
    re.IGNORECASE,
)

_WEEKDAY_NAMES: dict[str, list[str]] = {
    "EN": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "ES": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
    "FR": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
}


def _resolve_vars(
    text: str,
    tz_name: str | None,
    workspace_root: str | None,
    channel: str | None = None,
    chat_id: str | None = None,
    context_id: str | None = None,
    sender_name: str | None = None,
    sender_username: str | None = None,
    sender_first_name: str | None = None,
    sender_last_name: str | None = None,
) -> str:
    """Reemplaza variables dinámicas en el prompt con valores reales en runtime.

    Las variables ``{{CHANNEL.SENDER}}``, ``{{CHANNEL.USERNAME}}``,
    ``{{CHANNEL.FIRST_NAME}}`` y ``{{CHANNEL.LAST_NAME}}`` se resuelven sólo cuando el
    adapter inyecta los valores correspondientes en ``ChannelContext``. Si el valor
    es ``None`` (canal sin sender claro, usuario sin username configurado, etc.) la
    variable queda literal en el prompt — mismo criterio que ``{{WORKSPACE}}``.

    ``{{CHANNEL.CONTEXTID}}`` resuelve a la clave estable de la entidad de contexto
    (``ChannelContext.context_id`` = ``chat_id or user_id``) — la misma que nombra el
    fichero de memoria caliente. Queda literal sólo si no hay ``ChannelContext`` (turno
    sin canal, ej. scheduler).
    """
    if not _VAR_RE.search(text):
        return text

    # tzinfo | None es el tipo común: ZoneInfo cuando hay match, fallback al
    # tz del proceso si tz_name no resuelve o está vacío (puede ser None si el
    # sistema no expone tzinfo).
    tz: tzinfo | None
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
        # {{CHANNEL}} es alias de {{CHANNEL.NAME}}.
        if token in ("CHANNEL", "CHANNEL.NAME"):
            return channel if channel else m.group(0)
        if token == "CHANNEL.CHATID":
            return chat_id if chat_id else m.group(0)
        if token == "CHANNEL.CONTEXTID":
            return context_id if context_id else m.group(0)
        if token == "CHANNEL.SENDER":
            return sender_name if sender_name else m.group(0)
        if token == "CHANNEL.USERNAME":
            return sender_username if sender_username else m.group(0)
        if token == "CHANNEL.FIRST_NAME":
            return sender_first_name if sender_first_name else m.group(0)
        if token == "CHANNEL.LAST_NAME":
            return sender_last_name if sender_last_name else m.group(0)
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
    # Canal y chat_id del turno actual. Vacío/None → {{CHANNEL.*}} intacto (mismo criterio que WORKSPACE).
    channel: str | None = None
    chat_id: str | None = None
    # Clave estable de la entidad de contexto (ChannelContext.context_id = chat_id or user_id).
    # Nombra el fichero de memoria caliente; expuesta como {{CHANNEL.CONTEXTID}}. None → literal.
    context_id: str | None = None
    # Identidad del remitente humano del turno (Telegram private chat, etc.). Cada campo
    # puede ser None de forma independiente: el adapter rellena lo que conoce y deja en
    # None lo que no aplica. En grupos quedan todos None — la identidad se embebe en el
    # contenido del mensaje (ver ``format_group_message``).
    sender_name: str | None = None
    sender_username: str | None = None
    sender_first_name: str | None = None
    sender_last_name: str | None = None
    # Fragmentos de conocimiento recuperados por KnowledgeOrchestrator para este turno.
    knowledge_chunks: list[KnowledgeChunk] = []

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

        if self.knowledge_chunks:
            # Agrupar por source_id manteniendo el orden de aparición
            grupos: dict[str, list[KnowledgeChunk]] = {}
            for chunk in self.knowledge_chunks:
                grupos.setdefault(chunk.source_id, []).append(chunk)

            bloque = "\n## Relevant Knowledge\n"
            for source_id, fragmentos in grupos.items():
                bloque += f"\n### {source_id}\n"
                for fragmento in fragmentos:
                    bloque += f"- [{fragmento.score:.2f}] {fragmento.content}\n"
            sections.append(bloque)

        if extra_sections:
            for section in extra_sections:
                sections.append(section)

        return _resolve_vars(
            "\n".join(sections),
            self.timezone,
            self.workspace_root,
            self.channel,
            self.chat_id,
            context_id=self.context_id,
            sender_name=self.sender_name,
            sender_username=self.sender_username,
            sender_first_name=self.sender_first_name,
            sender_last_name=self.sender_last_name,
        )
