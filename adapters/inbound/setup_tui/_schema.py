"""Helpers de inferencia de tipos del schema Pydantic para la TUI de setup.

Infieren el ``kind`` editable de un campo (scalar / enum / long / secret / bool),
su default y las opciones de un ``Literal`` a partir de la anotación y el nombre.
Son consumidos por ``_schema_tree.build_schema_tree`` (TUI v3, árbol split-pane).

Reglas de inferencia de kind:
  - ``Literal[...]``              → ``"enum"`` con las opciones del Literal.
  - ``bool`` (o ``bool | None``)  → ``"bool"`` (toggle, detectado por tipo exacto).
  - nombre sugiere secret         → ``"secret"`` (api_key, token, auth, auth_key, password, secret).
  - nombre sugiere texto largo    → ``"long"`` (system_prompt, description, body).
  - otro                          → ``"scalar"``.
"""

from __future__ import annotations

from typing import Any, Literal, get_args, get_origin

from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from adapters.inbound.setup_tui.domain.field import FieldKind

# Palabras clave que indican que un campo es un secret
_SECRET_KEYWORDS = ("api_key", "token", "auth_key", "auth", "password", "secret")

# Palabras clave que indican que un campo es un texto largo
_LONG_KEYWORDS = ("system_prompt", "description", "body", "content", "text")


def _field_is_secret(field_info: FieldInfo | None) -> bool:
    """True si el campo está marcado como secreto en el schema.

    FUENTE DE VERDAD explícita: ``Field(json_schema_extra={"secret": True})``.
    Reemplaza la heurística por nombre (frágil): un campo es secreto porque el
    schema lo declara, no porque su nombre se parezca a uno.
    """
    if field_info is None:
        return False
    extra = field_info.json_schema_extra
    return isinstance(extra, dict) and bool(extra.get("secret"))


def _name_suggests_secret(name: str) -> bool:
    """True si el NOMBRE del campo sugiere un secreto.

    Ya NO se usa en runtime (la verdad es ``_field_is_secret`` sobre el marcador
    del schema). Se conserva solo para el guard de tests, que detecta campos con
    nombre sospechoso sin marcar — un olvido que dejaría un secreto sin enmascarar.
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


def _infer_kind(name: str, annotation: Any, field_info: FieldInfo | None = None) -> FieldKind:
    """Infiere el kind del campo a partir de la anotación, el marcador de secreto
    del schema y el nombre (solo para texto largo)."""
    unwrapped = _unwrap_optional(annotation)

    if _is_literal(unwrapped):
        return "enum"
    # ``bool`` se detecta por tipo exacto (identidad), antes que las heurísticas:
    # un campo booleano nunca debe caer en secret/long/scalar porque se edita con
    # un toggle, no tipeando "true"/"false". ``is`` evita que un ``int`` matchee
    # (bool es subclase de int, pero la anotación es distinta).
    if unwrapped is bool:
        return "bool"
    # Secreto = lo que el schema MARCA (no lo que el nombre sugiere).
    if _field_is_secret(field_info):
        return "secret"
    if _is_long(name):
        return "long"
    return "scalar"
