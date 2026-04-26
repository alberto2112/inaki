"""SectionHeader — título de sección en MAYÚSCULAS con estilo muted."""

from __future__ import annotations

from textual.widgets import Static


class SectionHeader(Static):
    """Título de sección en MAYÚSCULAS, color muted, altura 2."""

    DEFAULT_CSS = """
    SectionHeader {
        height: 2;
        padding: 1 2 0 2;
        color: $text-muted;
        text-style: bold;
    }
    """
