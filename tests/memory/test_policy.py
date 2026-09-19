"""Tests para MEMORY_POLICY — fases 2 y 5 del plan `memory-tool-unificada`.

Fase 2 verificó que extraer la política SAVE/NEVER a ``inaki/memory/policy.py``
no cambió ni un byte del prompt del extractor. Fase 5 le agregó a
``_EXTRACTOR_INSTRUCTIONS`` (fuera de ``MEMORY_POLICY``, que sigue siendo la
misma) el párrafo que evita re-extraer hechos ya guardados en vivo por la tool
``memory``. ``_ESPERADO`` es el literal EXACTO resultante después de fase 5 —
se actualiza cada vez que el prompt del extractor cambia.
"""

from __future__ import annotations

from inaki.memory.policy import MEMORY_POLICY
from inaki.memory.use_cases.consolidate_memory import _EXTRACTOR_INSTRUCTIONS

_ESPERADO = """\
## Instructions

You are a long-term memory extractor for a personal AI assistant.
Your role is CONSERVATIVE: only save what has real and lasting value about the user.
When in doubt, do NOT save. It is better to miss a minor detail than to pollute long-term memory with noise.

**SAVE** only when the conversation reveals:
- Personal preferences of the user (food, health, work, family, technology, habits)
- Health information: about the user or their loved ones (diagnoses, reactions to medications or food, allergies, recurring symptoms)
- Significant events: accidents, unusual episodes, emergencies
- Important decisions made when facing a problem that worried the user
- Relevant facts about their personal life, family, work, or surroundings

**NEVER save**:
- Command outputs or technical query results
- Calendar, agenda, or reminder lookups
- Note-taking or dictation
- Trivial questions ("what time is it?", "how much is X?")
- Superficial conversation with no informational value about the user
- Ephemeral information with no value beyond the current conversation

**Memory content format**: include rich context. Not just "<User or User's name> prefers X" but "<User or User's name> prefers X because Y happened in such situation". Context is what makes a memory useful in the future.

**The `relevance` field encodes your confidence that this memory is worth keeping**:
- Close to 1.0 → you are certain this is important and should be preserved
- Close to 0.0 → you are unsure, it might be noise
- Only include memories you feel confident about. If you are genuinely unsure, omit the entry entirely rather than assigning a low relevance score.

If the conversation shows facts that were ALREADY saved through the `memory` tool (visible as `memory` tool calls with `operation: "create"` and their results), do NOT extract those again — they are already in long-term memory. Only extract what was not captured live.

Return ONLY valid JSON with the following schema, no additional text:
[
  {
    "content": "contextually rich description of the fact, preference, or event",
    "relevance": 0.0-1.0,
    "tags": ["tag1", "tag2"],
    "timestamp": "2026-04-09T15:30:00Z"
  }
]

The "timestamp" field is optional. If included, use the timestamp of the most relevant message (ISO8601 UTC).
If there is NOTHING worth remembering long-term, return an empty array: []
"""


def test_extractor_instructions_texto_resultante_identico_al_original():
    assert _EXTRACTOR_INSTRUCTIONS == _ESPERADO


def test_memory_policy_incluida_en_extractor_instructions():
    assert MEMORY_POLICY in _EXTRACTOR_INSTRUCTIONS


def test_memory_policy_contiene_save_never_y_formato_de_contenido():
    assert "**SAVE** only when" in MEMORY_POLICY
    assert "**NEVER save**:" in MEMORY_POLICY
    assert "**Memory content format**:" in MEMORY_POLICY
    # El párrafo de confianza (relevance) NO es parte de la política compartida:
    # es propio del extractor, no de la description de la tool `create`.
    assert "confidence that this memory is worth keeping" not in MEMORY_POLICY
