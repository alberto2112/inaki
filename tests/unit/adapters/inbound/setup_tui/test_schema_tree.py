"""Tests del builder ``build_schema_tree`` (TUI v3).

Verifica la regla central "solo lo presente en el YAML es un nodo; lo ausente
es ``addable``", la recursión a N niveles, el caso especial ``channels`` y la
preservación del tri-estado.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field as PydField

from adapters.inbound.setup_tui._schema_tree import build_schema_tree
from adapters.inbound.setup_tui.domain.schema_node import SchemaNode


# --------------------------------------------------------------------------
# Modelos sintéticos para aislar la lógica del builder
# --------------------------------------------------------------------------


class _Groups(BaseModel):
    behavior: str = "mention"
    rate_limiter: int = 5


class _Telegram(BaseModel):
    model_config = ConfigDict(extra="allow")
    token: str = ""
    groups: _Groups | None = None


class _Llm(BaseModel):
    provider: str = "anthropic"
    model: str = "x"


class _Agent(BaseModel):
    id: str
    name: str
    llm: _Llm | None = None
    channels: dict[str, dict] = PydField(default_factory=dict)
    providers: dict[str, dict] = PydField(default_factory=dict)


_CHANNEL_SCHEMAS = {"telegram": _Telegram}


def _hijo(node: SchemaNode, key: str) -> SchemaNode:
    """Devuelve el hijo con esa clave (falla si no existe)."""
    return next(c for c in node.children if c.key == key)


def _addable_keys(node: SchemaNode) -> set[str]:
    return {o.key for o in node.addable}


# --------------------------------------------------------------------------
# Regla central: solo lo presente es nodo, lo ausente es addable
# --------------------------------------------------------------------------


def test_solo_presentes_son_nodos_y_ausentes_son_addable():
    tree = build_schema_tree(
        _Agent,
        {"id": "a", "name": "Anacleto"},
        root_label="anacleto",
        channel_schemas=_CHANNEL_SCHEMAS,
        exclude_keys=frozenset({"providers"}),
    )
    child_keys = {c.key for c in tree.children}
    # Presentes en el YAML → nodos
    assert child_keys == {"id", "name"}
    # Ausentes → addable (llm como sección, channels como sección)
    assert _addable_keys(tree) == {"llm", "channels"}
    # llm y channels son secciones añadibles
    assert all(o.is_section for o in tree.addable)


def test_hoja_presente_lleva_field_editable():
    tree = build_schema_tree(
        _Agent, {"id": "abc", "name": "N"}, root_label="r", channel_schemas=_CHANNEL_SCHEMAS
    )
    id_node = _hijo(tree, "id")
    assert id_node.is_section is False
    assert id_node.field is not None
    assert id_node.field.value == "abc"
    assert id_node.path == ("id",)


# --------------------------------------------------------------------------
# Sub-secciones BaseModel
# --------------------------------------------------------------------------


def test_subseccion_basemodel_presente_recursa():
    tree = build_schema_tree(
        _Agent,
        {"id": "a", "name": "N", "llm": {"provider": "openai"}},
        root_label="r",
        channel_schemas=_CHANNEL_SCHEMAS,
        exclude_keys=frozenset({"providers"}),
    )
    llm = _hijo(tree, "llm")
    assert llm.is_section is True
    assert llm.path == ("llm",)
    # provider presente → nodo con valor; model ausente → addable
    assert _hijo(llm, "provider").field.value == "openai"  # type: ignore[union-attr]
    assert "model" in _addable_keys(llm)
    assert "llm" not in _addable_keys(tree)  # ya no es addable: está presente


# --------------------------------------------------------------------------
# Caso especial channels + N niveles
# --------------------------------------------------------------------------


def test_channels_presente_resuelve_canal_tipado():
    tree = build_schema_tree(
        _Agent,
        {"id": "a", "name": "N", "channels": {"telegram": {"token": "T"}}},
        root_label="r",
        channel_schemas=_CHANNEL_SCHEMAS,
        exclude_keys=frozenset({"providers"}),
    )
    channels = _hijo(tree, "channels")
    assert channels.is_section is True
    telegram = _hijo(channels, "telegram")
    assert telegram.path == ("channels", "telegram")
    # token presente; groups ausente → addable de telegram
    assert _hijo(telegram, "token").field.value == "T"  # type: ignore[union-attr]
    assert "groups" in _addable_keys(telegram)


def test_channels_tres_niveles_groups():
    tree = build_schema_tree(
        _Agent,
        {
            "id": "a",
            "name": "N",
            "channels": {"telegram": {"token": "T", "groups": {"behavior": "autonomous"}}},
        },
        root_label="r",
        channel_schemas=_CHANNEL_SCHEMAS,
        exclude_keys=frozenset({"providers"}),
    )
    groups = _hijo(_hijo(_hijo(tree, "channels"), "telegram"), "groups")
    assert groups.path == ("channels", "telegram", "groups")
    assert groups.is_section is True
    assert _hijo(groups, "behavior").field.value == "autonomous"  # type: ignore[union-attr]
    # rate_limiter ausente → addable
    assert "rate_limiter" in _addable_keys(groups)


def test_channels_addable_lista_canales_faltantes():
    tree = build_schema_tree(
        _Agent, {"id": "a", "name": "N"}, root_label="r", channel_schemas=_CHANNEL_SCHEMAS
    )
    # channels ausente del todo → addable en la raíz
    assert "channels" in _addable_keys(tree)


def test_canal_desconocido_se_ignora():
    tree = build_schema_tree(
        _Agent,
        {"id": "a", "name": "N", "channels": {"slack": {"x": 1}}},
        root_label="r",
        channel_schemas=_CHANNEL_SCHEMAS,
        exclude_keys=frozenset({"providers"}),
    )
    channels = _hijo(tree, "channels")
    # slack no está en el registry → sin hijo; telegram sigue siendo addable
    assert channels.children == []
    assert "telegram" in _addable_keys(channels)


# --------------------------------------------------------------------------
# Tri-estado y extra keys
# --------------------------------------------------------------------------


def test_tristate_se_marca_por_path_dotted():
    tree = build_schema_tree(
        _Agent,
        {"id": "a", "name": "N", "llm": {"provider": "openai"}},
        root_label="r",
        channel_schemas=_CHANNEL_SCHEMAS,
        tristate_paths=frozenset({"llm.provider"}),
        exclude_keys=frozenset({"providers"}),
    )
    provider = _hijo(_hijo(tree, "llm"), "provider")
    assert provider.field.is_tristate is True  # type: ignore[union-attr]
    assert provider.field.tristate_state == "override_value"  # type: ignore[union-attr]


def test_dynamic_enums_fuerza_enum_en_provider():
    """Un campo str libre cuyo nombre está en dynamic_enums se vuelve enum."""
    tree = build_schema_tree(
        _Agent,
        {"id": "a", "name": "N", "llm": {"provider": "openai", "model": "x"}},
        root_label="r",
        channel_schemas=_CHANNEL_SCHEMAS,
        exclude_keys=frozenset({"providers"}),
        dynamic_enums={"provider": ("openai", "anthropic", "groq")},
    )
    provider = _hijo(_hijo(tree, "llm"), "provider")
    assert provider.field.kind == "enum"  # type: ignore[union-attr]
    assert provider.field.enum_choices == ("openai", "anthropic", "groq")  # type: ignore[union-attr]
    # 'model' no está en dynamic_enums → sigue libre (scalar)
    assert _hijo(_hijo(tree, "llm"), "model").field.kind == "scalar"  # type: ignore[union-attr]


def test_tristate_gana_sobre_dynamic_enum():
    """Un campo tri-estado (memories.llm.provider) NO se convierte en enum aunque
    'provider' esté en dynamic_enums — el tri-estado tiene prioridad."""
    tree = build_schema_tree(
        _Agent,
        {"id": "a", "name": "N", "llm": {"provider": "openai"}},
        root_label="r",
        channel_schemas=_CHANNEL_SCHEMAS,
        tristate_paths=frozenset({"llm.provider"}),
        dynamic_enums={"provider": ("openai", "anthropic")},
    )
    provider = _hijo(_hijo(tree, "llm"), "provider")
    assert provider.field.is_tristate is True  # type: ignore[union-attr]
    assert provider.field.kind != "enum"  # type: ignore[union-attr]


def test_exclude_keys_oculta_providers():
    tree = build_schema_tree(
        _Agent,
        {"id": "a", "name": "N", "providers": {"openai": {"api_key": "k"}}},
        root_label="r",
        channel_schemas=_CHANNEL_SCHEMAS,
        exclude_keys=frozenset({"providers"}),
    )
    assert "providers" not in {c.key for c in tree.children}
    assert "providers" not in _addable_keys(tree)


def test_extra_key_no_declarada_se_muestra_como_hoja():
    tree = build_schema_tree(
        _Telegram,
        {"token": "T", "campo_raro": "valor"},
        root_label="telegram",
        channel_schemas=_CHANNEL_SCHEMAS,
    )
    raro = _hijo(tree, "campo_raro")
    assert raro.is_section is False
    assert raro.field.value == "valor"  # type: ignore[union-attr]


# --------------------------------------------------------------------------
# Integración con el schema real
# --------------------------------------------------------------------------


def test_integracion_con_agentconfig_real():
    from infrastructure.config import AgentConfig, TelegramChannelConfig

    valores = {
        "id": "anacleto",
        "name": "Anacleto",
        "channels": {
            "telegram": {
                "token": "TKN",
                "groups": {"behavior": "autonomous", "bot_username": "anacleto_ia_bot"},
            }
        },
    }
    tree = build_schema_tree(
        AgentConfig,
        valores,
        root_label="anacleto",
        channel_schemas={"telegram": TelegramChannelConfig},
        exclude_keys=frozenset({"providers"}),
    )
    telegram = _hijo(_hijo(tree, "channels"), "telegram")
    groups = _hijo(telegram, "groups")
    assert _hijo(groups, "behavior").field.value == "autonomous"  # type: ignore[union-attr]
    assert _hijo(groups, "bot_username").field.value == "anacleto_ia_bot"  # type: ignore[union-attr]
    # broadcast no está → addable en telegram
    assert "broadcast" in _addable_keys(telegram)
