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

from inaki.embedding.base import BaseEmbeddingProvider, ResolvedEmbeddingConfig
from inaki.shared.errors import EmbeddingError

PROVIDER_NAME = "openai"

logger = logging.getLogger(__name__)

# Reintentos ante errores TRANSITORIOS (429 rate-limit, 5xx). Los permanentes
# (401 api_key inválida, 400 payload roto) fallan al instante — reintentarlos
# sería al pedo. Backoff exponencial + jitter, mismo idioma que broadcast/tcp.py.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_INTENTOS = 4
_ESPERA_BASE_SEG = 1.0
_ESPERA_MAX_SEG = 30.0

# Tope de la entrada, en CARACTERES (FIX bg-delivery-sin-routing). text-embedding-3-small
# acepta 8192 tokens y rechaza con 400 lo que pase; un resultado de delegación de 60.388
# chars lo hizo en producción. Sin tokenizer (no se agrega ``tiktoken``) el tope es una
# estimación conservadora: el español anda en ~4 chars/token, el código y el JSON en ~3,
# así que 24.000 chars son ~8000 tokens en el PEOR de esos casos — bajo el límite. Se
# conserva la CABEZA: la intención de una consulta y el tema de un recuerdo viven al
# principio. Lo que aun así se pase (emoji, CJK, base64: <2 chars/token) lo absorbe la
# red de seguridad de ``_embed`` (reintento con la mitad).
MAX_CHARS_ENTRADA = 24_000
_MAX_RECORTES_POR_400 = 2

# Largo máximo del cuerpo de la respuesta de error que se vuelca al mensaje: lo bastante
# para ver el motivo que da OpenAI, sin llenar el log. Nunca se incluyen headers.
_MAX_CHARS_CUERPO_ERROR = 300


class _ContextoExcedido(EmbeddingError):
    """OpenAI rechazó la entrada por exceder el contexto del modelo (400 específico)."""


def _es_contexto_excedido(resp: httpx.Response) -> bool:
    """¿Es este 400 un "la entrada es demasiado larga"?

    Forma real de OpenAI: ``{"error": {"message": ..., "type": "invalid_request_error",
    "code": "context_length_exceeded" | null}}``. El ``code`` no siempre viene (en
    embeddings suele ser ``null`` y el motivo solo está en el mensaje: "This model's
    maximum context length is 8192 tokens..." / "maximum input length is 8192
    tokens"), así que se miran los dos, y un cuerpo que no sea ese JSON cuenta como
    "otro 400" en vez de romper.
    """
    try:
        error = resp.json().get("error") or {}
    except (ValueError, AttributeError):
        return False
    if not isinstance(error, dict):
        return False
    if error.get("code") == "context_length_exceeded":
        return True
    mensaje = str(error.get("message") or "").lower()
    return any(
        pista in mensaje
        for pista in ("maximum context length", "maximum input length", "reduce your prompt")
    )


def _cuerpo_resumido(resp: httpx.Response) -> str:
    """Cuerpo de la respuesta truncado, para que el error se pueda diagnosticar."""
    texto = resp.text.strip()
    if len(texto) > _MAX_CHARS_CUERPO_ERROR:
        return texto[:_MAX_CHARS_CUERPO_ERROR] + "…"
    return texto


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

    async def _embed(self, text: str, *, es_passage: bool) -> list[float]:
        """Embebe ``text`` acotado a ``MAX_CHARS_ENTRADA``, con red de seguridad ante 400.

        Truncar una query es lo normal (DEBUG); truncar un passage pierde contenido
        que se indexa (WARNING, con los dos largos).
        """
        original = len(text)
        if original > MAX_CHARS_ENTRADA:
            text = text[:MAX_CHARS_ENTRADA]
            self._avisar_recorte(es_passage, original, len(text))
        for recorte in range(_MAX_RECORTES_POR_400 + 1):
            try:
                return await self._solicitar(text)
            except _ContextoExcedido:
                if recorte == _MAX_RECORTES_POR_400:
                    raise
                anterior = len(text)
                text = text[: anterior // 2]
                self._avisar_recorte(es_passage, original, len(text))
        # Inalcanzable: el último intento retorna o lanza. Guard para mypy.
        raise EmbeddingError("OpenAI embeddings: reintentos agotados sin resultado")

    @staticmethod
    def _avisar_recorte(es_passage: bool, original: int, recortado: int) -> None:
        if es_passage:
            logger.warning(
                "OpenAI embeddings: passage recortado de %d a %d chars; el embedding "
                "no cubre el final del contenido indexado",
                original,
                recortado,
            )
        else:
            logger.debug("OpenAI embeddings: query recortada de %d a %d chars", original, recortado)

    async def _solicitar(self, text: str) -> list[float]:
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
                detalle = f"{exc} — respuesta: {_cuerpo_resumido(exc.response)}"
                if status == 400 and _es_contexto_excedido(exc.response):
                    raise _ContextoExcedido(f"OpenAI embeddings HTTP error: {detalle}") from exc
                # Permanente (401/400/etc.) o último intento agotado → fallar ya.
                if status not in _RETRYABLE_STATUS or intento == _MAX_INTENTOS:
                    raise EmbeddingError(f"OpenAI embeddings HTTP error: {detalle}") from exc
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
        return await self._embed(text, es_passage=False)

    async def embed_passage(self, text: str) -> list[float]:
        # text-embedding-3-small no requiere prefijos — texto directo
        return await self._embed(text, es_passage=True)
