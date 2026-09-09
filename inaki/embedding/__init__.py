"""Embedding — el vectorizador que comparten routing de tools, skills, memoria y knowledge.

Providers (``e5_onnx`` local, ``openai``) auto-descubiertos por ``PROVIDER_NAME``,
``cache`` SQLite de embeddings y ``similarity`` (coseno). El port
``IEmbeddingProvider`` lo posee el kernel.
"""

from __future__ import annotations

import sys

from core.ports.outbound.embedding_port import IEmbeddingProvider


def resolve_provider_name(embedder: IEmbeddingProvider) -> str:
    """Obtiene el PROVIDER_NAME del módulo del embedder."""
    module = sys.modules.get(type(embedder).__module__)
    return getattr(module, "PROVIDER_NAME", type(embedder).__name__)
