"""Tests de interacción de TreeEditorPage con Pilot (capa Textual real).

A diferencia del resto del setup_tui (que evita Pilot por el conflicto entre
``asyncio_mode=auto`` y el event-loop de Textual), estos tests envuelven la
corrida en ``asyncio.run`` DENTRO de una función de test síncrona — así no
chocan con el modo auto de pytest-asyncio.

Cubren la regresión que el rediseño introdujo y que ningún test "puro" detectó:
la navegación del árbol y el descenso al panel dependían de bindings con
``priority`` que le robaban las teclas al widget ``Tree``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.widgets import Tree

from adapters.inbound.setup_tui.app import SetupApp
from adapters.inbound.setup_tui.di import build_setup_container
from adapters.inbound.setup_tui.widgets.detail_row import DetailRow
from infrastructure.config import AgentConfig, GlobalConfig, TelegramChannelConfig


def _container(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config"
    (cfg / "agents").mkdir(parents=True)
    (cfg / "global.yaml").write_text("app:\n  name: Inaki\nllm:\n  provider: x\n  model: y\n", "utf-8")
    (cfg / "agents" / "anacleto.yaml").write_text(
        "id: anacleto\nname: Anacleto\nllm:\n  provider: anthropic\n  model: sonnet\n"
        "channels:\n  telegram:\n    token: T\n    groups:\n      behavior: autonomous\n",
        "utf-8",
    )
    monkeypatch.setenv("INAKI_HOME", str(tmp_path))
    (tmp_path / "setup_welcome_seen").touch()  # saltar el modal de bienvenida
    return build_setup_container(
        config_dir=cfg,
        global_schema=GlobalConfig,
        agent_schema=AgentConfig,
        channel_schemas={"telegram": TelegramChannelConfig},
    )


async def _abrir_global(pilot):
    await pilot.pause()
    await pilot.press("enter")  # GLOBAL CONFIG (primer item del menú)
    await pilot.pause()
    await pilot.pause()


async def _abrir_agente(pilot):
    await pilot.pause()
    await pilot.press("down", "enter")  # AGENTS
    await pilot.pause()
    await pilot.press("enter")  # primer agente
    await pilot.pause()
    await pilot.pause()


def test_global_monta_arbol_con_secciones_presentes(tmp_path, monkeypatch):
    container = _container(tmp_path, monkeypatch)

    async def _run():
        app = SetupApp(container)
        async with app.run_test() as pilot:
            await _abrir_global(pilot)
            assert type(app.screen).__name__ == "GlobalPage"
            tree = app.screen.query_one("#nav", Tree)
            hijos = [str(n.label) for n in tree.root.children]
            assert "app" in hijos and "llm" in hijos

    asyncio.run(_run())


def test_flechas_navegan_el_arbol(tmp_path, monkeypatch):
    """Regresión del bug: ↓ debe mover el cursor del Tree (no quedar clavado)."""
    container = _container(tmp_path, monkeypatch)

    async def _run():
        app = SetupApp(container)
        async with app.run_test() as pilot:
            await _abrir_global(pilot)
            tree = app.screen.query_one("#nav", Tree)
            assert str(tree.cursor_node.label) == "global"
            await pilot.press("down")
            await pilot.pause()
            assert str(tree.cursor_node.label) != "global"  # el cursor se movió

    asyncio.run(_run())


def test_channels_aparece_en_el_arbol_del_agente(tmp_path, monkeypatch):
    """El día-cero del rediseño: channels (dict crudo) ahora es navegable."""
    container = _container(tmp_path, monkeypatch)

    async def _run():
        app = SetupApp(container)
        async with app.run_test() as pilot:
            await _abrir_agente(pilot)
            assert type(app.screen).__name__ == "AgentDetailPage"
            tree = app.screen.query_one("#nav", Tree)
            labels = {str(n.label) for n in _walk(tree.root)}
            assert {"channels", "telegram", "groups"} <= labels

    asyncio.run(_run())


def test_enter_baja_al_panel_y_escape_vuelve(tmp_path, monkeypatch):
    """Enter sobre una sección con ítems enfoca el panel; Esc vuelve al árbol."""
    container = _container(tmp_path, monkeypatch)

    async def _run():
        app = SetupApp(container)
        async with app.run_test() as pilot:
            await _abrir_global(pilot)
            await pilot.press("down")  # -> 'app'
            await pilot.pause()
            await pilot.pause()
            assert any(app.screen.query(DetailRow))  # el panel se pobló
            await pilot.press("enter")  # bajar al panel
            await pilot.pause()
            assert app.screen._focus_zone == "detail"
            await pilot.press("escape")  # volver al árbol
            await pilot.pause()
            assert app.screen._focus_zone == "tree"

    asyncio.run(_run())


def test_enter_sobre_campo_presente_abre_editor(tmp_path, monkeypatch):
    """Bug original: Enter sobre un campo no editaba. Ahora abre el modal."""
    container = _container(tmp_path, monkeypatch)

    async def _run():
        app = SetupApp(container)
        async with app.run_test() as pilot:
            await _abrir_agente(pilot)
            await pilot.press("down")  # -> 'llm' (provider/model presentes)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("enter")  # bajar al panel (primer campo)
            await pilot.pause()
            await pilot.press("enter")  # editar el campo
            await pilot.pause()
            # se abrió un modal de edición encima de la página
            assert type(app.screen).__name__.startswith("Edit")

    asyncio.run(_run())


def test_enter_sobre_opcion_add_crea_la_clave(tmp_path, monkeypatch):
    """Enter sobre '+ campo' añade la clave y aparece como campo presente."""
    container = _container(tmp_path, monkeypatch)

    async def _run():
        app = SetupApp(container)
        async with app.run_test() as pilot:
            await _abrir_agente(pilot)
            # navegar a groups (tiene 'behavior' presente + addables como bot_username)
            await pilot.press("down", "down", "down", "down")  # llm, channels, telegram, groups
            await pilot.pause()
            await pilot.pause()
            page = app.screen
            assert page._current_section.key == "groups"
            # bajar al panel y buscar el primer ítem 'add'
            await pilot.press("enter")
            await pilot.pause()
            idx_add = next(i for i, (k, _) in enumerate(page._detail_items) if k == "add")
            for _ in range(idx_add):
                await pilot.press("down")
            await pilot.pause()
            opt = page._detail_items[idx_add][1]
            await pilot.press("enter")  # añadir esa opción
            await pilot.pause()
            await pilot.pause()
            # tras repintar, esa clave ahora es un campo presente del árbol/panel
            presentes = {leaf.label for leaf in page._current_section.leaf_children}
            assert opt.key in presentes

    asyncio.run(_run())


def _walk(node):
    yield node
    for c in node.children:
        yield from _walk(c)
