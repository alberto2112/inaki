"""Utilidades compartidas para adaptadores de embedding."""

from __future__ import annotations

import sys

from core.ports.outbound.embedding_port import IEmbeddingProvider


def resolve_provider_name(embedder: IEmbeddingProvider) -> str:
    """Obtiene el PROVIDER_NAME del módulo del embedder."""
    module = sys.modules.get(type(embedder).__module__)
    return getattr(module, "PROVIDER_NAME", type(embedder).__name__)
