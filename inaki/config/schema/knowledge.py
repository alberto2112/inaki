"""Bloque ``knowledge``: fuentes RAG (harness-global).

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

from pydantic import ConfigDict
from inaki.config.schema._base import RuntimePath, ExpandedPath, _ConfigBaseModel


class KnowledgeSourceConfig(_ConfigBaseModel):
    """Configuración de una fuente de conocimiento externa."""

    id: str
    """Identificador único de la fuente (usado para rutas de DB y CLI)."""

    type: str
    """Tipo de fuente: 'document' | 'sqlite'."""

    enabled: bool = True
    """Si False, la fuente se ignora al construir el KnowledgeOrchestrator."""

    description: str = ""
    """Descripción de la fuente (inyectada en el system prompt)."""

    path: ExpandedPath | None = None
    """Ruta al directorio de documentos (solo para type='document')."""

    glob: str = "**/*.md"
    """Glob pattern para seleccionar archivos (solo para type='document')."""

    chunk_size: int = 500
    """Tamaño de cada chunk en palabras (solo para type='document')."""

    chunk_overlap: int = 80
    """Solapamiento entre chunks en palabras (solo para type='document')."""

    top_k: int = 3
    """Resultados máximos a recuperar de esta fuente por turno."""

    min_score: float = 0.5
    """Score mínimo de coseno para incluir un chunk."""


class KnowledgeConfig(_ConfigBaseModel):
    """Configuración global del pipeline de knowledge pre-fetch."""

    model_config = ConfigDict(validate_default=True)

    enabled: bool = True
    """Si False, el pre-fetch se saltea completamente en cada turno."""

    db_dirname: RuntimePath = "knowledge"
    """Directorio (relativo al home de instancia) de las DBs de índice por fuente:
    ``<home>/knowledge/{source_id}.db``. Se reancla con ``--home`` / ``INAKI_HOME``."""

    include_memory: bool = True
    """Si True, la memoria SQLite del agente se registra como fuente automáticamente."""

    top_k_per_source: int = 3
    """top_k global por fuente cuando no se override por fuente individual."""

    min_score: float = 0.5
    """min_score global cuando no se override por fuente individual."""

    max_total_chunks: int = 10
    """Límite duro de chunks totales tras el fan-out (ordenados por score desc)."""

    token_budget_warn_threshold: int = 4000
    """Umbral estimado de tokens totales (chunks + digest + skills). Si se supera,
    se emite un WARNING con el desglose. 0 = deshabilita la advertencia."""

    sources: list[KnowledgeSourceConfig] = []
    """Lista de fuentes de conocimiento externas configuradas."""
