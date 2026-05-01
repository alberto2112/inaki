"""Tests de introspección Pydantic para BroadcastEmitConfig en la TUI.

Verifica dos cosas:

1. Cuando se introspeccionan ``BroadcastConfig``, el sub-modelo
   ``BroadcastEmitConfig`` aparece como sección editable con sus 3 flags.
   Esto garantiza que el day-zero requirement está cumplido en el modelo
   Pydantic — la TUI puede renderizar los flags si se le pasa BroadcastConfig.

2. Documenta la limitación actual: ``AgentConfig.channels`` es un
   ``dict[str, dict[str, Any]]`` y el schema mapper SALTA dicts. Por lo
   tanto, ``broadcast.emit.*`` (igual que el resto de ``channels.*``) NO
   aparece en la TUI cuando se introspecciona desde ``AgentConfig``.
   Esta limitación es preexistente y aplica a todo el bloque ``channels``;
   resolverla requiere un refactor separado que tipifique ``channels``.
"""

from __future__ import annotations

from adapters.inbound.setup_tui._schema import sections_for_model
from infrastructure.config import AgentConfig, BroadcastConfig


def test_broadcast_config_introspeccion_directa_expone_emit():
    """Pasando BroadcastConfig directo, la TUI ve la sub-sección EMIT con sus 3 flags."""
    sections = sections_for_model(BroadcastConfig, {})
    section_names = [name for name, _ in sections]

    assert "EMIT" in section_names, (
        f"BroadcastEmitConfig debería aparecer como sección 'EMIT' al introspeccionar "
        f"BroadcastConfig. Secciones encontradas: {section_names}"
    )

    emit_fields = next(fields for name, fields in sections if name == "EMIT")
    field_labels = {f.label for f in emit_fields}

    assert field_labels == {
        "assistant_response",
        "user_input_voice",
        "user_input_photo",
    }, f"EMIT debería tener exactamente los 3 flags. Encontrados: {field_labels}"


def test_agent_config_no_introspecciona_channels_limitacion_conocida():
    """Documenta la limitación: AgentConfig.channels (dict[str, Any]) no se introspecciona.

    Si este test empieza a fallar (es decir, CHANNELS aparece en las secciones),
    significa que alguien tipificó ``AgentConfig.channels`` — gran noticia. En
    ese caso, actualizar la TUI para mapear las nuevas secciones nested de
    broadcast.emit y borrar este test.
    """
    sections = sections_for_model(AgentConfig, {})
    section_names = [name for name, _ in sections]

    # channels es dict[str, Any] → schema mapper lo skippea por _SKIP_ORIGINS
    assert "CHANNELS" not in section_names
    # Por lo tanto broadcast tampoco aparece (está nested dentro de channels)
    assert not any(
        "BROADCAST" in name or "EMIT" in name or "CHANNEL" in name for name in section_names
    )


def test_broadcast_emit_default_values_legibles_via_introspeccion():
    """Los valores default de BroadcastEmitConfig se exponen correctamente en los Field."""
    sections = sections_for_model(BroadcastConfig, {})
    emit_fields = next(fields for name, fields in sections if name == "EMIT")
    by_label = {f.label: f for f in emit_fields}

    assert by_label["assistant_response"].default == "True"
    assert by_label["user_input_voice"].default == "False"
    assert by_label["user_input_photo"].default == "False"
