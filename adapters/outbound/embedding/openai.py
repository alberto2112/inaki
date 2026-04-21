"""
Proveedor de embeddings via OpenAI API (text-embedding-3-small con MRL).

Usa el parámetro `dimensions` de la API para obtener vectores de 384d
(Matryoshka Representation Learning) en lugar de los 1536d por defecto.
Esto mantiene compatibilidad con el schema SQLite existente (FLOAT[384])
sin necesidad de migración.

A diferencia de E5, este modelo NO requiere prefijos query:/passage:.
embed_query() y embed_passage() envían el texto tal cual.
"""

from __future__ import annotations

import logging

import httpx

from adapters.outbound.embedding.base import BaseEmbeddingProvider
from core.domain.errors import EmbeddingError
from infrastructure.config import ResolvedEmbeddingConfig

PROVIDER_NAME = "openai"

logger = logging.getLogger(__name__)


class OpenAIEmbeddingProvider(BaseEmbeddingProvider):
    _DEFAULT_BASE_URL = "https://api.openai.com/v1"

    def __init__(self, cfg: ResolvedEmbeddingConfig) -> None:
        if not cfg.api_key:
            raise EmbeddingError("OpenAI embedding requiere api_key en providers.openai.api_key")
        self._cfg = cfg
        self._base_url = cfg.base_url or self._DEFAULT_BASE_URL
        self._headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        }

    async def _embed(self, text: str) -> list[float]:
        payload = {
            "model": self._cfg.model,
            "input": text,
            "dimensions": self._cfg.dimension,
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._base_url}/embeddings",
                    headers=self._headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"OpenAI embeddings HTTP error: {exc}") from exc

        return data["data"][0]["embedding"]

    async def embed_query(self, text: str) -> list[float]:
        # text-embedding-3-small no requiere prefijos — texto directo
        return await self._embed(text)

    async def embed_passage(self, text: str) -> list[float]:
        # text-embedding-3-small no requiere prefijos — texto directo
        return await self._embed(text)
