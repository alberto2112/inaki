"""
Tests unitarios para DiffPreview (función calcular_diff).

Usamos la función pura ``calcular_diff`` para los golden tests —
evitamos la instanciación del widget Textual en tests unitarios.
"""

from __future__ import annotations

from adapters.inbound.setup_tui.widgets.diff_preview import calcular_diff


# ---------------------------------------------------------------------------
# Golden tests con diffs conocidos
# ---------------------------------------------------------------------------


class TestCalcularDiff:
    """Tests con diffs de entrada/salida conocidos."""

    def test_sin_cambios_retorna_vacio(self) -> None:
        yaml = "app:\n  name: inaki\n"
        assert calcular_diff(yaml, yaml) == ""

    def test_agrega_campo_nuevo(self) -> None:
        antes = "app:\n  name: inaki\n"
        despues = "app:\n  name: inaki\n  debug: true\n"
        diff = calcular_diff(antes, despues)
        assert "+  debug: true" in diff

    def test_modifica_campo_existente(self) -> None:
        antes = "llm:\n  model: gpt-4\n"
        despues = "llm:\n  model: gpt-4o\n"
        diff = calcular_diff(antes, despues)
        assert "-  model: gpt-4" in diff
        assert "+  model: gpt-4o" in diff

    def test_elimina_campo(self) -> None:
        antes = "llm:\n  model: gpt-4\n  temperature: 0.7\n"
        despues = "llm:\n  model: gpt-4\n"
        diff = calcular_diff(antes, despues)
        assert "-  temperature: 0.7" in diff

    def test_encabezado_contiene_etiqueta_antes(self) -> None:
        antes = "a: 1\n"
        despues = "a: 2\n"
        diff = calcular_diff(antes, despues, etiqueta="global")
        assert "global (disco)" in diff

    def test_encabezado_contiene_etiqueta_despues(self) -> None:
        antes = "a: 1\n"
        despues = "a: 2\n"
        diff = calcular_diff(antes, despues, etiqueta="agent/dev")
        assert "agent/dev (pendiente)" in diff

    def test_diff_multilinea_complejo(self) -> None:
        antes = (
            "app:\n"
            "  name: inaki\n"
            "  debug: false\n"
            "llm:\n"
            "  model: gpt-4\n"
            "  provider: openai\n"
        )
        despues = (
            "app:\n"
            "  name: inaki\n"
            "  debug: true\n"
            "llm:\n"
            "  model: gpt-4o-mini\n"
            "  provider: openai\n"
        )
        diff = calcular_diff(antes, despues)
        assert "-  debug: false" in diff
        assert "+  debug: true" in diff
        assert "-  model: gpt-4" in diff
        assert "+  model: gpt-4o-mini" in diff
        # Línea sin cambios NO debe aparecer como + ni -
        assert "  name: inaki" in diff  # línea de contexto

    def test_golden_diff_conocido(self) -> None:
        """Test golden exacto para verificar el formato de salida."""
        antes = "x: 1\n"
        despues = "x: 2\n"
        diff = calcular_diff(antes, despues, etiqueta="test")
        # unified_diff produce "-x: 1" (sin espacio después del guión para líneas de contenido)
        tiene_menos = "-x: 1" in diff
        tiene_mas = "+x: 2" in diff
        assert tiene_menos, f"Falta '-x: 1' en diff:\n{diff}"
        assert tiene_mas, f"Falta '+x: 2' en diff:\n{diff}"

    def test_strings_identicos_no_generan_diff(self) -> None:
        contenido = "providers:\n  openai:\n    base_url: https://api.openai.com\n"
        assert calcular_diff(contenido, contenido) == ""


# ---------------------------------------------------------------------------
# Tests de propiedades del DiffPreview widget (sin montar en headless)
# ---------------------------------------------------------------------------


class TestDiffPreviewTieneCAmbios:
    """Verifica la propiedad ``tiene_cambios`` del widget sin montarlo."""

    def test_sin_datos_no_tiene_cambios(self) -> None:
        from adapters.inbound.setup_tui.widgets.diff_preview import DiffPreview

        dp = DiffPreview()
        assert not dp.tiene_cambios

    def test_con_datos_iguales_no_tiene_cambios(self) -> None:
        from adapters.inbound.setup_tui.widgets.diff_preview import DiffPreview

        dp = DiffPreview()
        dp._antes = "a: 1\n"
        dp._despues = "a: 1\n"
        assert not dp.tiene_cambios

    def test_con_datos_distintos_tiene_cambios(self) -> None:
        from adapters.inbound.setup_tui.widgets.diff_preview import DiffPreview

        dp = DiffPreview()
        dp._antes = "a: 1\n"
        dp._despues = "a: 2\n"
        assert dp.tiene_cambios
