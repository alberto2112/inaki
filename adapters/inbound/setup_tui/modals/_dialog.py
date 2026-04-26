"""CSS y helpers compartidos para todos los modales de edición."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adapters.inbound.setup_tui.domain.field import Field


def initial_value_for_input(field: "Field") -> str:
    """Texto a pre-cargar en el Input/TextArea cuando se abre un modal.

    Prioridad:
      1. El valor actual del campo (si está configurado y no es vacío).
      2. El default declarado en el Pydantic (si existe).
      3. String vacío.

    Tratamiento explícito de ``0`` y ``False``: se consideran VALORES
    configurados (no caen al default) — el usuario los seteó a propósito.
    """
    if field.value not in (None, ""):
        return str(field.value)
    return field.default or ""


def dialog_css(class_name: str) -> str:
    """Genera el CSS común de un modal: centrado, borde recto, fondo dark.

    Args:
        class_name: Nombre del selector CSS de la clase modal (sin punto).

    Returns:
        String de CSS listo para usar como ``DEFAULT_CSS`` en el modal.
    """
    return f"""
    {class_name} {{
        align: center middle;
    }}
    {class_name} #dialog {{
        width: 70;
        height: auto;
        max-height: 22;
        padding: 1 2;
        border: solid $accent;
        background: #161616;
    }}
    {class_name} .titulo {{
        height: 1;
        color: $accent;
        text-style: bold;
    }}
    {class_name} .footer {{
        height: 1;
        margin-top: 1;
        color: $text-muted;
    }}
    """
