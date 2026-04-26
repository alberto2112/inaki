"""Dataclass Field — descriptor de un campo editable en la TUI de setup."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# Tipos de campo soportados por los modales de edición.
FieldKind = Literal["scalar", "enum", "long", "secret"]

# Estados posibles del tri-estado (cuando is_tristate=True).
TristateEstado = Literal["inherit", "override_value", "override_null"]


@dataclass
class Field:
    """Descriptor de un campo editable en la TUI de setup.

    Atributos:
        label: Nombre del campo tal como se muestra en la fila.
        value: Valor actual (mutable — se actualiza tras cada edición).
        kind: Tipo de editor que se lanza al editar este campo.
        enum_choices: Opciones válidas para campos de tipo ``"enum"``.
        default: Default del schema Pydantic; se muestra en dim si el campo está vacío.
        is_tristate: Si ``True``, el campo soporta 3 estados:
            ``"inherit"`` (heredar del global), ``"override_value"`` (valor explícito)
            y ``"override_null"`` (null explícito). Ortogonal a ``kind``.
        tristate_state: Estado triestado actual del campo. Solo relevante cuando
            ``is_tristate=True``. Puede ser ``None`` si aún no se determinó.
    """

    label: str
    value: Any
    kind: FieldKind
    enum_choices: tuple[str, ...] | None = field(default=None)
    default: str | None = field(default=None)
    is_tristate: bool = field(default=False)
    tristate_state: TristateEstado | None = field(default=None)
