"""Tests Pilot del modal de providers rediseñado (elegir adaptador de lista).

Verifican el flujo nuevo: el adaptador se elige de un Select (no se escribe), el
nombre se autocompleta y el ``type`` se deriva (implícito si nombre==adaptador).
Patrón asyncio.run dentro de test síncrono (ver test_tree_editor_pilot).
"""

from __future__ import annotations

import asyncio

from textual.widgets import Input, Select

from adapters.inbound.setup_tui.screens.providers_page import _EditProviderModal


def test_footer_de_providers_anuncia_nuevo_y_eliminar():
    """El footer de ProvidersPage debe listar las teclas n (nuevo) y delete."""
    from adapters.inbound.setup_tui.screens.providers_page import ProvidersPage

    texto = ProvidersPage.__new__(ProvidersPage).status_text()
    assert "nuevo" in texto and "eliminar" in texto


def test_select_autocompleta_el_nombre_y_deriva_type_implicito():
    """Elegir 'groq' → el nombre se autocompleta a 'groq' → type queda implícito."""
    capturado: dict = {}

    async def _run():
        from textual.app import App

        class _Host(App):
            def on_mount(self) -> None:
                self.push_screen(
                    _EditProviderModal(adapters=("openai", "groq", "ollama"), edit_mode=False),
                    lambda r: capturado.update(r or {}),
                )

        app = _Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            modal = app.screen
            modal.query_one("#input_adapter", Select).value = "groq"
            await pilot.pause()
            # el nombre se autocompletó al elegir el adaptador
            assert modal.query_one("#input_key", Input).value == "groq"
            modal.action_commit()
            await pilot.pause()

    asyncio.run(_run())
    assert capturado["key"] == "groq"
    assert capturado["type"] == ""  # implícito: nombre == adaptador


def test_nombre_distinto_del_adaptador_persiste_type():
    """Caso avanzado: nombre 'openai-work' con adaptador 'openai' → type='openai'."""
    capturado: dict = {}

    async def _run():
        from textual.app import App

        class _Host(App):
            def on_mount(self) -> None:
                self.push_screen(
                    _EditProviderModal(adapters=("openai", "groq"), edit_mode=False),
                    lambda r: capturado.update(r or {}),
                )

        app = _Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            modal = app.screen
            modal.query_one("#input_adapter", Select).value = "openai"
            await pilot.pause()
            modal.query_one("#input_key", Input).value = "openai-work"
            modal.action_commit()
            await pilot.pause()

    asyncio.run(_run())
    assert capturado["key"] == "openai-work"
    assert capturado["type"] == "openai"  # explícito: nombre != adaptador


def test_edit_mode_preselecciona_adaptador_y_bloquea_nombre():
    """Al editar, el Select muestra el adaptador actual y el nombre no es editable."""

    async def _run():
        from textual.app import App

        class _Host(App):
            def on_mount(self) -> None:
                self.push_screen(
                    _EditProviderModal(
                        adapters=("openai", "groq"),
                        key="groq",
                        type_val="",
                        edit_mode=True,
                    )
                )

        app = _Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            modal = app.screen
            # type vacío → el adaptador efectivo es la key
            assert modal.query_one("#input_adapter", Select).value == "groq"
            assert modal.query_one("#input_key", Input).disabled is True

    asyncio.run(_run())
