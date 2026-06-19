"""Builder del árbol de configuración (``SchemaNode``) para la TUI v3.

Convierte un ``BaseModel`` de Pydantic + los valores actuales del YAML en un
árbol donde **solo viven las claves presentes**. Para cada nodo sección computa
además las opciones ``addable`` (claves del schema no presentes) — eso alimenta
el modal de "añadir sección/campo".

Reusa los helpers de inferencia de tipos de ``_schema.py`` (``_infer_kind``,
``_unwrap_optional``, ...) — NO se duplican.

Caso especial ``channels``: en ``AgentConfig`` el campo es ``dict[str, dict]``
(no un ``BaseModel``), así que no se puede introspeccionar solo. El builder recibe
``channel_schemas`` (``{"telegram": TelegramChannelConfig, ...}``) inyectado por el
composition root y trata cada canal como una sub-sección tipada.
"""

from __future__ import annotations

import inspect
from typing import Any, get_args, get_origin

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from adapters.inbound.setup_tui._schema import (
    _default_as_str,
    _field_is_secret,
    _infer_kind,
    _literal_choices,
    _unwrap_optional,
)
from adapters.inbound.setup_tui.domain.field import Field
from adapters.inbound.setup_tui.domain.schema_node import AddableOption, SchemaNode


def build_schema_tree(
    model: type[BaseModel],
    current_values: dict[str, Any],
    *,
    root_label: str,
    channel_schemas: dict[str, type[BaseModel]] | None = None,
    tristate_paths: frozenset[str] | None = None,
    exclude_keys: frozenset[str] = frozenset(),
    dynamic_enums: dict[str, tuple[str, ...]] | None = None,
) -> SchemaNode:
    """Construye el árbol de config a partir del schema y los valores actuales.

    Args:
        model: Modelo Pydantic raíz (``AgentConfig`` / ``GlobalConfig``).
        current_values: Valores actuales (capa cruda o efectiva, según la página).
        root_label: Etiqueta del nodo raíz (ej. ``"anacleto"`` o ``"global"``).
        channel_schemas: Registry ``nombre_canal → modelo`` para resolver el dict
            ``channels``. ``None`` desactiva el tratamiento especial de canales.
        tristate_paths: Paths (dotted lowercase, ej. ``"memories.llm.provider"``)
            cuyas hojas se marcan ``is_tristate=True``.
        exclude_keys: Claves del nivel raíz a ignorar (ej. ``providers``, que
            tiene su propia página). Se aplican en cualquier nivel.

    Returns:
        El ``SchemaNode`` raíz (sección, ``path == ()``).
    """
    return _build_section(
        model,
        current_values if isinstance(current_values, dict) else {},
        path=(),
        label=root_label,
        channel_schemas=channel_schemas or {},
        tristate_paths=tristate_paths or frozenset(),
        exclude_keys=exclude_keys,
        dynamic_enums=dynamic_enums or {},
    )


def _build_section(
    model: type[BaseModel],
    values: dict[str, Any],
    *,
    path: tuple[str, ...],
    label: str,
    channel_schemas: dict[str, type[BaseModel]],
    tristate_paths: frozenset[str],
    exclude_keys: frozenset[str],
    dynamic_enums: dict[str, tuple[str, ...]],
) -> SchemaNode:
    """Construye un nodo sección y recursa sobre sus hijos presentes."""
    children: list[SchemaNode] = []
    addable: list[AddableOption] = []

    for name, field_info in model.model_fields.items():
        if name in exclude_keys:
            continue
        annotation = field_info.annotation
        if annotation is None:
            continue

        present = name in values
        descripcion = field_info.description or ""

        # --- Caso especial: channels (dict[str, dict]) con registry inyectado ---
        if name == "channels" and channel_schemas and _es_tipo_dict(annotation):
            if present:
                children.append(
                    _build_channels_node(
                        values.get(name) or {},
                        path=path + (name,),
                        channel_schemas=channel_schemas,
                        tristate_paths=tristate_paths,
                        exclude_keys=exclude_keys,
                        dynamic_enums=dynamic_enums,
                    )
                )
            else:
                addable.append(AddableOption(name, name, is_section=True, description=descripcion))
            continue

        unwrapped = _unwrap_optional(annotation)

        # --- Sub-sección tipada (BaseModel anidado) ---
        if inspect.isclass(unwrapped) and issubclass(unwrapped, BaseModel):
            if present:
                sub_values = values.get(name)
                children.append(
                    _build_section(
                        unwrapped,
                        sub_values if isinstance(sub_values, dict) else {},
                        path=path + (name,),
                        label=name,
                        channel_schemas=channel_schemas,
                        tristate_paths=tristate_paths,
                        exclude_keys=exclude_keys,
                        dynamic_enums=dynamic_enums,
                    )
                )
            else:
                addable.append(AddableOption(name, name, is_section=True, description=descripcion))
            continue

        # --- Hoja editable (scalar, enum, bool, list, dict genérico) ---
        if present:
            children.append(
                _build_leaf(
                    name,
                    annotation,
                    field_info,
                    values,
                    path=path,
                    tristate_paths=tristate_paths,
                    dynamic_enums=dynamic_enums,
                )
            )
        else:
            addable.append(
                AddableOption(
                    name,
                    name,
                    is_section=False,
                    description=descripcion,
                    default_value=_default_value(field_info),
                    is_secret=_field_is_secret(field_info),
                )
            )

    # Claves presentes en el YAML que el schema no declara (extra="allow"): se
    # muestran como hojas genéricas para no esconder config del usuario.
    declaradas = set(model.model_fields.keys())
    for extra_key in values:
        if extra_key in declaradas or extra_key in exclude_keys:
            continue
        children.append(
            SchemaNode(
                path=path + (extra_key,),
                label=extra_key,
                is_section=False,
                present=True,
                field=Field(label=extra_key, value=_safe_value(values[extra_key]), kind="scalar"),
            )
        )

    return SchemaNode(
        path=path,
        label=label,
        is_section=True,
        present=True,
        children=children,
        addable=addable,
    )


def _build_channels_node(
    channels_values: dict[str, Any],
    *,
    path: tuple[str, ...],
    channel_schemas: dict[str, type[BaseModel]],
    tristate_paths: frozenset[str],
    exclude_keys: frozenset[str],
    dynamic_enums: dict[str, tuple[str, ...]],
) -> SchemaNode:
    """Construye el nodo ``channels``: una sección cuyos hijos son los canales
    presentes (tipados vía ``channel_schemas``) y cuyos ``addable`` son los
    canales del registry todavía no configurados."""
    children: list[SchemaNode] = []
    for canal_name, canal_values in channels_values.items():
        schema = channel_schemas.get(canal_name)
        if schema is None:
            continue  # canal desconocido para el registry → no introspectable
        children.append(
            _build_section(
                schema,
                canal_values if isinstance(canal_values, dict) else {},
                path=path + (canal_name,),
                label=canal_name,
                channel_schemas=channel_schemas,
                tristate_paths=tristate_paths,
                exclude_keys=exclude_keys,
                dynamic_enums=dynamic_enums,
            )
        )

    addable = [
        AddableOption(nombre, nombre, is_section=True)
        for nombre in channel_schemas
        if nombre not in channels_values
    ]

    return SchemaNode(
        path=path,
        label="channels",
        is_section=True,
        present=True,
        children=children,
        addable=addable,
    )


def _build_leaf(
    name: str,
    annotation: Any,
    field_info: Any,
    values: dict[str, Any],
    *,
    path: tuple[str, ...],
    tristate_paths: frozenset[str],
    dynamic_enums: dict[str, tuple[str, ...]],
) -> SchemaNode:
    """Construye un nodo hoja (campo editable) reusando la inferencia de kind.

    Si el nombre del campo está en ``dynamic_enums``, se fuerza ``kind="enum"``
    con esos choices aunque el schema lo declare como ``str`` libre (ej.
    ``provider`` → adaptadores autodescubiertos). Los tri-estado no se tocan.
    """
    unwrapped = _unwrap_optional(annotation)
    kind = _infer_kind(name, annotation, field_info)
    enum_choices = _literal_choices(unwrapped) if kind == "enum" else None

    ruta_dotted = ".".join(path + (name,))
    es_tristate = ruta_dotted in tristate_paths
    if name in dynamic_enums and not es_tristate:
        kind = "enum"
        enum_choices = dynamic_enums[name]

    default_str = _default_as_str(field_info)

    raw = values.get(name, "")
    value = raw if raw is not None else ""

    is_tristate = es_tristate
    tristate_state = None
    if is_tristate:
        if name not in values:
            tristate_state = "inherit"
        elif values.get(name) is None:
            tristate_state = "override_null"
        else:
            tristate_state = "override_value"

    return SchemaNode(
        path=path + (name,),
        label=name,
        is_section=False,
        present=True,
        field=Field(
            label=name,
            value=value,
            kind=kind,
            enum_choices=enum_choices,
            default=default_str,
            is_tristate=is_tristate,
            tristate_state=tristate_state,  # type: ignore[arg-type]
        ),
    )


def _es_tipo_dict(annotation: Any) -> bool:
    """``True`` si la anotación (desenvuelta de Optional) es un ``dict[...]``."""
    return get_origin(_unwrap_optional(annotation)) is dict


def _dict_value_model(annotation: Any) -> type[BaseModel] | None:
    """Si la anotación es ``dict[str, X]`` con ``X`` un ``BaseModel``, devuelve X."""
    args = get_args(_unwrap_optional(annotation))
    if len(args) == 2:
        v = args[1]
        if inspect.isclass(v) and issubclass(v, BaseModel):
            return v
    return None


# Path del secreto, ¿configurado?, valor actual (si lo hay).
DeclaredSecret = tuple[tuple[str, ...], bool, Any]


def iter_declared_secrets(
    model: type[BaseModel],
    values: dict[str, Any],
    *,
    channel_schemas: dict[str, type[BaseModel]] | None = None,
    path: tuple[str, ...] = (),
) -> list[DeclaredSecret]:
    """Recorre ``model`` + ``values`` y recolecta TODOS los campos secretos
    declarados (marcados en el schema), con su estado de configuración.

    Permite a la SecretsPage ser proactiva: enumera qué secretos EXISTEN según
    el schema y la estructura configurada, no solo los que ya están escritos.
    ``configured`` = la clave está presente y no vacía. Maneja ``channels``
    (heterogéneo, vía ``channel_schemas``) y cualquier ``dict[str, BaseModel]``
    (homogéneo, ej. ``providers``) recursando por cada entrada presente.

    LÍMITE DELIBERADO: solo cubre secretos del SCHEMA de config (providers,
    channels, admin, photos.scene...). Los secretos de **tools y skills quedan
    FUERA por diseño** — su config es conversacional vía el Tool Config Protocol
    (``config_namespace`` + ``IToolConfigStore``), le da libertad al dev y no hay
    sección de tools/skills en el setup TUI donde mostrarlos. NO "completar" esto
    para tools: no es un olvido, es el borde del sistema.
    """
    out: list[DeclaredSecret] = []
    values = values if isinstance(values, dict) else {}
    channel_schemas = channel_schemas or {}

    for name, field_info in model.model_fields.items():
        annotation = field_info.annotation
        if annotation is None:
            continue

        if _field_is_secret(field_info):
            configurado = name in values and values.get(name) not in (None, "")
            out.append((path + (name,), configurado, values.get(name)))
            continue

        sub = _unwrap_optional(annotation)

        if name == "channels" and channel_schemas and _es_tipo_dict(annotation):
            for canal, cval in (values.get("channels") or {}).items():
                schema = channel_schemas.get(canal)
                if schema is not None:
                    out += iter_declared_secrets(
                        schema,
                        cval,
                        channel_schemas=channel_schemas,
                        path=path + ("channels", canal),
                    )
        elif _es_tipo_dict(annotation):
            vmodel = _dict_value_model(annotation)
            if vmodel is not None:
                for k, v in (values.get(name) or {}).items():
                    out += iter_declared_secrets(
                        vmodel, v, channel_schemas=channel_schemas, path=path + (name, k)
                    )
        elif name in values and inspect.isclass(sub) and issubclass(sub, BaseModel):
            # Solo se recursa en sub-secciones PRESENTES: un secreto de broadcast
            # no aplica si el usuario no activó broadcast (evita ruido de pendientes
            # de features no usadas). Al activar la sección, su secreto aparece.
            out += iter_declared_secrets(
                sub, values.get(name) or {}, channel_schemas=channel_schemas, path=path + (name,)
            )

    return out


def _safe_value(raw: Any) -> Any:
    """Normaliza un valor crudo de YAML para un ``Field`` hoja genérico."""
    return "" if raw is None else raw


def _default_value(field_info: Any) -> Any:
    """Default tipado de un campo del schema, para sembrar el alta de un campo.

    Prioridad: ``default`` explícito → ``default_factory()`` → ``None``.
    """
    if field_info.default is not PydanticUndefined:
        return field_info.default
    if field_info.default_factory is not None:
        try:
            return field_info.default_factory()
        except Exception:
            return None
    return None
