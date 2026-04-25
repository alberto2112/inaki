"""Pantalla de edición de la sección ``memory`` (flags top-level)."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class MemoryScreen(SectionEditorScreen):
    """Edita los campos top-level de la sección ``memory`` de ``global.yaml``."""

    SECTION_KEY = "memory"
    TITULO = "Memory — Memoria a largo plazo"
    CAMPOS = (
        FieldSpec(
            "db_filename",
            str,
            "Archivo SQLite de memoria (relativo a ~/.inaki/)",
            placeholder="data/inaki.db",
        ),
        FieldSpec(
            "default_top_k",
            int,
            "Recuerdos recuperados por RAG en cada turno",
            placeholder="5",
        ),
        FieldSpec(
            "digest_size",
            int,
            "Recuerdos más recientes volcados al digest tras /consolidate",
            placeholder="14",
        ),
        FieldSpec(
            "digest_filename",
            str,
            "Archivo markdown del digest (relativo a ~/.inaki/)",
            placeholder="mem/last_memories.md",
        ),
        FieldSpec(
            "min_relevance_score",
            float,
            "Umbral mínimo (0.0–1.0) para persistir un recuerdo",
            placeholder="0.5",
        ),
        FieldSpec(
            "schedule",
            str,
            "Cron para consolidación nocturna",
            placeholder="0 3 * * *",
        ),
        FieldSpec(
            "delay_seconds",
            int,
            "Pausa entre agentes durante la consolidación (seg)",
            placeholder="2",
        ),
        FieldSpec(
            "keep_last_messages",
            int,
            "Mensajes a preservar por agente tras consolidación (0 = usar fallback 84)",
            placeholder="0",
        ),
        FieldSpec(
            "channels_infused",
            str,
            "Canales cuyos mensajes se consolidan (vacío = todos)",
            placeholder="telegram, cli",
            es_lista=True,
        ),
    )
