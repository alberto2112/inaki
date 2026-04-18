"""
Proveedor de embeddings con multilingual-e5-small via ONNX Runtime.

Regla crítica: este modelo requiere prefijos explícitos.
- Queries del usuario: prefijo 'query: ' → usar embed_query()
- Documentos/recuerdos/skills: prefijo 'passage: ' → usar embed_passage()

Los prefijos se aplican internamente — los use cases nunca deben añadirlos.
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from pathlib import Path

import numpy as np

from adapters.outbound.embedding.base import BaseEmbeddingProvider
from core.domain.errors import EmbeddingError
from infrastructure.config import EmbeddingConfig

PROVIDER_NAME = "e5_onnx"

logger = logging.getLogger(__name__)


class E5OnnxProvider(BaseEmbeddingProvider):

    def __init__(self, cfg: EmbeddingConfig) -> None:
        self._cfg = cfg
        self._session = None
        self._tokenizer = None
        self._model_path = Path(cfg.model_dirname)
        self._dimension = cfg.dimension

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return

        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise EmbeddingError(
                "onnxruntime y tokenizers son requeridos para e5_onnx. "
                "Instalar con: pip install onnxruntime tokenizers"
            ) from exc

        model_file = self._model_path / "model.onnx"
        tokenizer_file = self._model_path / "tokenizer.json"

        if not model_file.exists():
            raise EmbeddingError(
                f"Modelo ONNX no encontrado: {model_file}. "
                "Descargar multilingual-e5-small desde HuggingFace."
            )
        if not tokenizer_file.exists():
            raise EmbeddingError(f"Tokenizer no encontrado: {tokenizer_file}")

        self._session = ort.InferenceSession(
            str(model_file),
            providers=["CPUExecutionProvider"],
        )
        self._tokenizer = Tokenizer.from_file(str(tokenizer_file))
        logger.info("E5OnnxProvider cargado desde %s", self._model_path)

    def _embed(self, text: str) -> list[float]:
        self._ensure_loaded()
        encoding = self._tokenizer.encode(text)
        input_ids = np.array([encoding.ids], dtype=np.int64)
        attention_mask = np.array([encoding.attention_mask], dtype=np.int64)
        token_type_ids = np.zeros_like(input_ids)

        outputs = self._session.run(
            None,
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )

        # Mean pooling sobre los token embeddings
        token_embeddings = outputs[0][0]  # (seq_len, hidden_size)
        mask = attention_mask[0].reshape(-1, 1)
        pooled = (token_embeddings * mask).sum(axis=0) / mask.sum()

        # Normalización L2
        norm = np.linalg.norm(pooled)
        if norm > 0:
            pooled = pooled / norm

        return pooled.tolist()

    async def embed_query(self, text: str) -> list[float]:
        prefixed = f"query: {text}"
        return await asyncio.get_event_loop().run_in_executor(None, self._embed, prefixed)

    async def embed_passage(self, text: str) -> list[float]:
        prefixed = f"passage: {text}"
        return await asyncio.get_event_loop().run_in_executor(None, self._embed, prefixed)
