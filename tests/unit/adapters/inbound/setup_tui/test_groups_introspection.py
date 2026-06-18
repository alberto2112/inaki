"""Introspección Pydantic de la política de respuesta en grupos para la TUI.

Tras la migración ``groups-vs-broadcast``, los campos ``behavior``,
``bot_username``, ``rate_limiter`` y ``rate_limiter_window`` viven en
``TelegramGroupsConfig`` (antes en ``BroadcastConfig``). Como la TUI deriva los
campos por introspección del schema (``_schema.sections_for_model``), el cambio
debe reflejarse solo: ``behavior`` aparece como ``enum`` bajo la sección de grupos
y desaparece de la introspección de broadcast. Estos tests son el guard de esa
garantía — si alguien re-mezcla los campos, fallan.
"""

from __future__ import annotations

from adapters.inbound.setup_tui._schema import sections_for_model
from infrastructure.config import BroadcastConfig, TelegramGroupsConfig

_ROOT = "TELEGRAMGROUPSCONFIG"


def test_groups_introspeccion_expone_politica_de_respuesta():
    """La TUI ve behavior (enum), bot_username, rate_limiter y rate_limiter_window
    al introspeccionar TelegramGroupsConfig."""
    sections = sections_for_model(TelegramGroupsConfig, {})
    section_names = [name for name, _ in sections]
    assert _ROOT in section_names, f"Falta la sección raíz de grupos. Secciones: {section_names}"

    root_fields = next(fields for name, fields in sections if name == _ROOT)
    by_label = {f.label: f for f in root_fields}

    assert "behavior" in by_label
    assert by_label["behavior"].kind == "enum"
    assert set(by_label["behavior"].enum_choices or ()) == {"listen", "mention", "autonomous"}

    assert {"bot_username", "rate_limiter", "rate_limiter_window"} <= set(by_label), (
        f"Faltan campos de política de respuesta. Encontrados: {sorted(by_label)}"
    )


def test_broadcast_config_ya_no_expone_politica_de_grupos():
    """Guard de regresión: la política de grupos NO debe reaparecer en broadcast."""
    sections = sections_for_model(BroadcastConfig, {})
    all_labels = {f.label for _, fields in sections for f in fields}

    assert all_labels.isdisjoint(
        {"behavior", "bot_username", "rate_limiter", "rate_limiter_window"}
    ), f"BroadcastConfig no debe exponer política de grupos. Labels: {sorted(all_labels)}"
