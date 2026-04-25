"""Pantalla de edición de la sección ``knowledge`` (solo flags top-level)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Label

from adapters.inbound.setup_tui.screens.sections._base import FieldSpec, SectionEditorScreen


class KnowledgeScreen(SectionEditorScreen):
    """
    Edita los flags top-level de la sección ``knowledge``.

    La sub-lista ``sources`` es V2 — no se edita aquí.
    Para editar ``sources``, editá ``~/.inaki/config/global.yaml`` directamente.
    """

    SECTION_KEY = "knowledge"
    TITULO = "Knowledge — Fuentes de conocimiento externas"
    CAMPOS = (
        FieldSpec(
            "enabled",
            bool,
            "Activar el pipeline de knowledge pre-fetch",
            placeholder="true",
        ),
        FieldSpec(
            "include_memory",
            bool,
            "Incluir la memoria SQLite como fuente automáticamente",
            placeholder="true",
        ),
        FieldSpec(
            "top_k_per_source",
            int,
            "Top-K global por fuente (cuando no hay override por fuente)",
            placeholder="3",
        ),
        FieldSpec(
            "min_score",
            float,
            "Score mínimo global (cuando no hay override por fuente)",
            placeholder="0.5",
        ),
        FieldSpec(
            "max_total_chunks",
            int,
            "Límite duro de chunks totales tras el fan-out",
            placeholder="10",
        ),
        FieldSpec(
            "token_budget_warn_threshold",
            int,
            "Tokens estimados para emitir WARNING de presupuesto (0 = desactivado)",
            placeholder="4000",
        ),
    )

    def compose(self) -> ComposeResult:
        """Agrega una nota al pie sobre sources."""
        yield from super().compose()

    def on_mount(self) -> None:
        super().on_mount()
        # Agregar nota sobre sources al final
        try:
            from textual.widgets import Static
            nota = Label(
                "[dim italic]Para editar `sources` manualmente, "
                "editá `~/.inaki/config/global.yaml` directamente "
                "(edición de sources en TUI: V2).[/dim italic]",
                markup=True,
            )
            self.query_one("ScrollableContainer").mount(nota)
        except Exception:
            pass
