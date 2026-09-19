"""
MEMORY_POLICY — política única SAVE / NEVER de la memoria a largo plazo.

Antes vivía duplicada dentro de ``_EXTRACTOR_INSTRUCTIONS``
(``inaki/memory/use_cases/consolidate_memory.py``). A partir de la tool
``memory`` unificada (fase 3 del plan `memory-tool-unificada`) hay un segundo
consumidor — la ``description`` de la operación ``create`` — que necesita el
MISMO criterio de qué vale la pena guardar. Se extrae acá para que cambiar el
criterio sea tocar UN solo lugar.

El extractor nocturno sigue componiendo ``_EXTRACTOR_INSTRUCTIONS`` a partir
de esta constante (texto resultante idéntico al de antes de la extracción —
ver ``tests/memory/test_policy.py``).
"""

from __future__ import annotations

MEMORY_POLICY = """\
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
"""
