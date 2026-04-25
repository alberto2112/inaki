"""Pantalla de edición de la sección ``embedding``."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class EmbeddingScreen(SectionEditorScreen):
    """Edita la sección ``embedding`` de ``global.yaml``."""

    SECTION_KEY = "embedding"
    TITULO = "Embedding — Modelo de embeddings para RAG"
    CAMPOS = (
        FieldSpec(
            "provider",
            str,
            "KEY del registry (e5_onnx o openai)",
            dropdown_source="providers",
            placeholder="e5_onnx",
        ),
        FieldSpec(
            "model_dirname",
            str,
            "Directorio del modelo ONNX (relativo a ~/.inaki/)",
            placeholder="models/e5-small",
        ),
        FieldSpec(
            "model",
            str,
            "Modelo OpenAI (solo si provider=openai)",
            placeholder="text-embedding-3-small",
        ),
        FieldSpec(
            "dimension",
            int,
            "Dimensión del vector de embedding (no cambiar sin recrear DB)",
            placeholder="384",
        ),
        FieldSpec(
            "cache_filename",
            str,
            "Archivo SQLite de caché (relativo a ~/.inaki/)",
            placeholder="data/embedding_cache.db",
        ),
    )
