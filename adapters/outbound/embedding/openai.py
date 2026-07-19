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

import asyncio
import logging
import random

import httpx

from adapters.outbound.embedding.base import BaseEmbeddingProvider, ResolvedEmbeddingConfig
from core.domain.errors import EmbeddingError

PROVIDER_NAME = "openai"

logger = logging.getLogger(__name__)

# Reintentos ante errores TRANSITORIOS (429 rate-limit, 5xx). Los permanentes
# (401 api_key inválida, 400 payload roto) fallan al instante — reintentarlos
# sería al pedo. Backoff exponencial + jitter, mismo idioma que broadcast/tcp.py.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_INTENTOS = 4
_ESPERA_BASE_SEG = 1.0
_ESPERA_MAX_SEG = 30.0


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
        for intento in range(1, _MAX_INTENTOS + 1):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        f"{self._base_url}/embeddings",
                        headers=self._headers,
                        json=payload,
                    )
                    resp.raise_for_status()
                    return resp.json()["data"][0]["embedding"]
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                # Permanente (401/400/etc.) o último intento agotado → fallar ya.
                if status not in _RETRYABLE_STATUS or intento == _MAX_INTENTOS:
                    raise EmbeddingError(f"OpenAI embeddings HTTP error: {exc}") from exc
                espera = self._espera_reintento(intento, exc.response)
                logger.warning(
                    "OpenAI embeddings %s transitorio, reintento %d/%d en %.1fs",
                    status,
                    intento,
                    _MAX_INTENTOS,
                    espera,
                )
                await asyncio.sleep(espera)
            except httpx.HTTPError as exc:
                # Errores de transporte (timeout, conexión): transitorios también.
                if intento == _MAX_INTENTOS:
                    raise EmbeddingError(f"OpenAI embeddings HTTP error: {exc}") from exc
                espera = self._espera_reintento(intento, None)
                logger.warning(
                    "OpenAI embeddings error de transporte, reintento %d/%d en %.1fs: %s",
                    intento,
                    _MAX_INTENTOS,
                    espera,
                    exc,
                )
                await asyncio.sleep(espera)

        # Inalcanzable: el loop retorna o lanza en cada rama. Guard para mypy.
        raise EmbeddingError("OpenAI embeddings: reintentos agotados sin resultado")

    @staticmethod
    def _espera_reintento(intento: int, resp: httpx.Response | None) -> float:
        """Backoff exponencial + jitter; respeta `Retry-After` si OpenAI lo manda."""
        if resp is not None:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    return min(float(retry_after), _ESPERA_MAX_SEG)
                except ValueError:
                    pass  # header en formato fecha HTTP — caemos al backoff.
        base = min(_ESPERA_BASE_SEG * (2 ** (intento - 1)), _ESPERA_MAX_SEG)
        return base + random.uniform(0, base * 0.2)

    async def embed_query(self, text: str) -> list[float]:
        # text-embedding-3-small no requiere prefijos — texto directo
        return await self._embed(text)

    async def embed_passage(self, text: str) -> list[float]:
        # text-embedding-3-small no requiere prefijos — texto directo
        return await self._embed(text)
