"""Dataclass Field — descriptor de un campo editable en la TUI de setup."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# Tipos de campo soportados por los modales de edición.
FieldKind = Literal["scalar", "enum", "long", "secret", "bool"]

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


# Strings que representan "verdadero" al interpretar un valor crudo como booleano.
# Cubre la forma nativa de Python (``True``), la de YAML (``true``) y las
# escrituras viejas que pudieron quedar como string ("1", "yes", "on", "sí").
_TRUTHY_STRINGS = frozenset({"true", "1", "yes", "on", "y", "sí", "si"})


def coerce_bool(value: Any) -> bool:
    """Interpreta un valor crudo como booleano para campos ``kind == "bool"``.

    El valor puede llegar como ``bool`` nativo (leído del YAML por ruamel), como
    string ("true"/"True"/"false" de una edición vieja o del default Pydantic
    ``str(True)``), o como número. Cualquier otra cosa se considera ``False``.

    Args:
        value: Valor crudo del campo (``field.value`` o ``field.default``).

    Returns:
        El booleano equivalente.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY_STRINGS
    return False
