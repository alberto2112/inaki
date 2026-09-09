"""Similitud coseno entre vectores de embeddings."""

from __future__ import annotations

import numpy as np


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Calcula la similitud coseno entre dos vectores."""
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    norm_a = np.linalg.norm(va)
    norm_b = np.linalg.norm(vb)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(va, vb) / (norm_a * norm_b))
