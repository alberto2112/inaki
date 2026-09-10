"""
Fragmento de conocimiento recuperado por una fuente externa.

Usado por KnowledgeOrchestrator para agregar resultados de múltiples fuentes
y por AgentContext para renderizar la sección "## Relevant Knowledge" en el prompt.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class KnowledgeChunk(BaseModel):
    """Fragmento de conocimiento con score de relevancia (coseno ∈ [-1, 1])."""

    source_id: str
    """Identificador de la fuente que produjo este fragmento (ej. 'memory', 'docs-proyecto')."""

    content: str
    """Texto del fragmento."""

    score: float = Field(ge=-1.0, le=1.0)
    """Score de similitud coseno. Rango [-1, 1]. Valores > 0 indican relevancia positiva."""

    metadata: dict = Field(default_factory=dict)
    """Metadatos arbitrarios de la fuente (ej. file_path, chunk_idx, created_at)."""
