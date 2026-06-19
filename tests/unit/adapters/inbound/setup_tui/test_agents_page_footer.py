"""Guard del footer de AgentsPage: debe anunciar sus teclas propias.

AgentsPage (AGENTS y SUBAGENTS, mismo widget) tiene bindings n/c/delete que la
StatusBar genérica no mostraba. status_text los declara — este test evita que se
pierdan en un refactor.
"""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.agents_page import AgentsPage


def test_footer_anuncia_nuevo_clonar_eliminar():
    texto = AgentsPage.__new__(AgentsPage).status_text()
    assert "nuevo" in texto
    assert "clonar" in texto
    assert "eliminar" in texto
