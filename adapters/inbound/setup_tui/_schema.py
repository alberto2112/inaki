"""Introspección de modelos Pydantic para generar lista de Fields editables.

Este módulo convierte un ``BaseModel`` de Pydantic en una lista de secciones
con sus campos editables, inferiendo el ``kind`` de cada campo desde el tipo
y el nombre del campo. Es el source of truth para la TUI — al agregar campos
al schema de Pydantic, la TUI los recoge automáticamente sin cambios adicionales.

Reglas de inferencia de kind:
  - ``Literal[...]``              → ``"enum"`` con las opciones del Literal.
  - nombre sugiere secret         → ``"secret"`` (api_key, token, auth, auth_key, password, secret).
  - nombre sugiere texto largo    → ``"long"`` (system_prompt, description, body).
  - otro                          → ``"scalar"``.

Los campos de tipo ``BaseModel`` (sub-secciones) se expanden como secciones
separadas con el nombre de la clave en MAYÚSCULAS. Si un campo de sub-sección
tiene a su vez sub-campos de tipo ``BaseModel``, se recursa un nivel adicional
usando ``deep_section_names`` para controlar qué nombres de campo se expanden.

Los campos de tipo ``dict``, ``list`` y similares no se renderizan en la TUI
(son demasiado complejos para edición inline).
"""

from __future__ import annotations

import inspect
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from adapters.inbound.setup_tui.domain.field import Field, FieldKind

# Palabras clave que indican que un campo es un secret
_SECRET_KEYWORDS = ("api_key", "token", "auth_key", "auth", "password", "secret")

# Palabras clave que indican que un campo es un texto largo
_LONG_KEYWORDS = ("system_prompt", "description", "body", "content", "text")

# Tipos Python que NO se renderizan en la TUI (demasiado complejos)
_SKIP_ORIGINS = (dict, list, set, frozenset)


def _is_secret(name: str) -> bool:
    """True si el nombre del campo sugiere que es un secret.

    Match estricto (igual o sufijo ``_kw``) para evitar falsos positivos:
    ``max_tokens`` contiene ``token`` como substring pero NO es un secret.
    """
    lower = name.lower()
    return any(lower == kw or lower.endswith(f"_{kw}") for kw in _SECRET_KEYWORDS)


def _is_long(name: str) -> bool:
    """True si el nombre del campo sugiere que es texto largo."""
    lower = name.lower()
    return any(lower == kw or lower.endswith(f"_{kw}") for kw in _LONG_KEYWORDS)


def _is_literal(annotation: Any) -> bool:
    """True si la anotación es ``Literal[...]``."""
    return get_origin(annotation) is Literal


def _literal_choices(annotation: Any) -> tuple[str, ...]:
    """Extrae las opciones de un ``Literal[...]`` como strings."""
    return tuple(str(a) for a in get_args(annotation))


def _unwrap_optional(annotation: Any) -> Any:
    """Desenvuelve ``Optional[X]`` / ``X | None`` → ``X``.

    Retorna la anotación sin cambios si no es Optional.
    """
    import types

    origin = get_origin(annotation)
    # Union en Python 3.10+ puede ser types.UnionType
    if origin is getattr(types, "UnionType", None) or str(origin) in (
        "typing.Union",
        "typing.Optional",
    ):
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _default_as_str(field_info: FieldInfo) -> str | None:
    """Convierte el default de Pydantic a str, o None si no tiene default."""
    default = field_info.default
    if default is PydanticUndefined or default is None:
        return None
    return str(default)


def _infer_kind(name: str, annotation: Any) -> FieldKind:
    """Infiere el kind del campo a partir del nombre y la anotación."""
    unwrapped = _unwrap_optional(annotation)

    if _is_literal(unwrapped):
        return "enum"
    if _is_secret(name):
        return "secret"
    if _is_long(name):
        return "long"
    return "scalar"


def _should_skip(annotation: Any) -> bool:
    """True si el campo debe omitirse en la TUI (dict, list, sub-BaseModel, etc.)."""
    unwrapped = _unwrap_optional(annotation)
    origin = get_origin(unwrapped)

    if origin in _SKIP_ORIGINS:
        return True

    # Tipo directo (sin genérico)
    if inspect.isclass(unwrapped):
        if issubclass(unwrapped, BaseModel):
            return True  # sub-secciones se manejan por separado
        if unwrapped in (dict, list, set, frozenset, tuple):
            return True

    return False


def _fields_for_model(
    model: type[BaseModel],
    current_values: dict[str, Any],
    *,
    tristate_prefix: str = "",
    tristate_paths: frozenset[str] | None = None,
) -> list[Field]:
    """Genera la lista de ``Field`` para los campos simples de un modelo.

    Los campos cuyo tipo es ``BaseModel`` (sub-sección) se omiten — se manejan
    con ``sections_for_model`` de forma recursiva.

    Args:
        model: Modelo Pydantic a introspeccionar.
        current_values: Valores actuales de la capa.
        tristate_prefix: Prefijo de la sección actual (MAYÚSCULAS) para construir
            la ruta del campo y detectar si es triestado.
        tristate_paths: Conjunto de rutas ``"SECCION.campo"`` (sección en MAYÚSCULAS,
            campo en minúsculas) para marcar como triestado.
    """
    tristate_paths = tristate_paths or frozenset()
    fields: list[Field] = []

    for name, field_info in model.model_fields.items():
        annotation = field_info.annotation
        if annotation is None:
            continue

        unwrapped = _unwrap_optional(annotation)

        # Sub-secciones → se procesan recursivamente en sections_for_model
        if inspect.isclass(unwrapped) and issubclass(unwrapped, BaseModel):
            continue

        if _should_skip(annotation):
            continue

        kind = _infer_kind(name, annotation)
        enum_choices = None
        if kind == "enum":
            enum_choices = _literal_choices(unwrapped)

        current_value = current_values.get(name, "")
        default_str = _default_as_str(field_info)

        # Determinar si el campo está marcado como triestado
        ruta = f"{tristate_prefix}.{name}" if tristate_prefix else name
        is_tristate = ruta in tristate_paths

        # Inferir el estado triestado actual desde el valor cargado
        tristate_state = None
        if is_tristate:
            if name not in current_values:
                tristate_state = "inherit"
            elif current_values.get(name) is None:
                tristate_state = "override_null"
            else:
                tristate_state = "override_value"

        fields.append(
            Field(
                label=name,
                value=current_value if current_value is not None else "",
                kind=kind,
                enum_choices=enum_choices,
                default=default_str,
                is_tristate=is_tristate,
                tristate_state=tristate_state,
            )
        )

    return fields


def sections_for_model(
    model: type[BaseModel],
    current_values: dict[str, Any],
    *,
    section_prefix: str = "",
    tristate_paths: frozenset[str] | None = None,
) -> list[tuple[str, list[Field]]]:
    """Genera la lista de secciones ``(nombre, [Field, ...])`` para un modelo.

    Para cada campo de tipo ``BaseModel`` en el modelo raíz, crea una sección
    con el nombre en MAYÚSCULAS y los campos editables del sub-modelo.

    Cuando un sub-modelo tiene a su vez sub-campos ``BaseModel``, se recursa un
    nivel más para exponerlos (por ejemplo, ``MemoryConfig.llm`` → sección
    ``MEMORYLLMOVERRIDE``).

    Los campos simples del modelo raíz van en una sección con el nombre del
    modelo (o ``section_prefix`` si se provee).

    Args:
        model: El modelo Pydantic a introspeccionar.
        current_values: Dict con los valores actuales mergeados de las capas.
        section_prefix: Nombre de la sección raíz. Si vacío, se usa el nombre
            del modelo en MAYÚSCULAS.
        tristate_paths: Conjunto de rutas ``"seccion.campo"`` (ambos en minúsculas)
            cuyos ``Field`` se marcan con ``is_tristate=True``.
            Ej: ``frozenset({"memoryllmoverride.provider", "memoryllmoverride.model"})``.

    Returns:
        Lista de ``(section_name, [Field, ...])`` lista para renderizar en la TUI.
    """
    tristate_paths = tristate_paths or frozenset()
    sections: list[tuple[str, list[Field]]] = []

    # Campos simples del modelo raíz
    root_name = section_prefix.upper() or model.__name__.upper()
    root_fields = _fields_for_model(
        model, current_values, tristate_prefix=root_name, tristate_paths=tristate_paths
    )
    if root_fields:
        sections.append((root_name, root_fields))

    # Sub-secciones (campos tipo BaseModel)
    for name, field_info in model.model_fields.items():
        annotation = field_info.annotation
        if annotation is None:
            continue
        unwrapped = _unwrap_optional(annotation)

        if not (inspect.isclass(unwrapped) and issubclass(unwrapped, BaseModel)):
            continue

        sub_values = current_values.get(name) or {}
        if not isinstance(sub_values, dict):
            sub_values = {}

        section_name = name.upper()
        sub_fields = _fields_for_model(
            unwrapped,
            sub_values,
            tristate_prefix=section_name,
            tristate_paths=tristate_paths,
        )
        if sub_fields:
            sections.append((section_name, sub_fields))

        # Recursar un nivel más para sub-sub-modelos (ej: MemoryConfig.llm).
        # Se usa la ruta "PADRE.HIJO" (ej: "MEMORY.LLM") como nombre de sección
        # para que sea legible y predecible sin depender del nombre de la clase.
        for sub_name, sub_field_info in unwrapped.model_fields.items():
            sub_ann = sub_field_info.annotation
            if sub_ann is None:
                continue
            sub_unwrapped = _unwrap_optional(sub_ann)
            if not (inspect.isclass(sub_unwrapped) and issubclass(sub_unwrapped, BaseModel)):
                continue

            nested_values = sub_values.get(sub_name) or {}
            if not isinstance(nested_values, dict):
                nested_values = {}

            # Nombre de sección basado en la ruta padre.hijo (MAYÚSCULAS)
            nested_section_name = f"{section_name}.{sub_name}".upper()
            nested_fields = _fields_for_model(
                sub_unwrapped,
                nested_values,
                tristate_prefix=nested_section_name,
                tristate_paths=tristate_paths,
            )
            if nested_fields:
                sections.append((nested_section_name, nested_fields))

    return sections
