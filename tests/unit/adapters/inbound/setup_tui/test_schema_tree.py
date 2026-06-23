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


def test_dynamic_choices_fuerza_enum_por_ruta():
    """Un campo str libre cuya RUTA está en dynamic_choices se vuelve enum."""
    tree = build_schema_tree(
        _Agent,
        {"id": "a", "name": "N", "llm": {"provider": "openai", "model": "x"}},
        root_label="r",
        channel_schemas=_CHANNEL_SCHEMAS,
        exclude_keys=frozenset({"providers"}),
        dynamic_choices={"llm.provider": ("openai", "anthropic", "groq")},
    )
    provider = _hijo(_hijo(tree, "llm"), "provider")
    assert provider.field.kind == "enum"  # type: ignore[union-attr]
    assert provider.field.enum_choices == ("openai", "anthropic", "groq")  # type: ignore[union-attr]
    # 'model' no está en dynamic_choices → sigue libre (scalar)
    assert _hijo(_hijo(tree, "llm"), "model").field.kind == "scalar"  # type: ignore[union-attr]


def test_dynamic_choices_mapea_por_ruta_no_por_nombre():
    """El mapeo es por ruta: mapear 'embedding.provider' NO afecta al 'provider'
    homónimo de 'llm'. (Antes, mapear por nombre pisaba todos los 'provider'.)"""
    tree = build_schema_tree(
        _Agent,
        {"id": "a", "name": "N", "llm": {"provider": "openai"}},
        root_label="r",
        channel_schemas=_CHANNEL_SCHEMAS,
        exclude_keys=frozenset({"providers"}),
        dynamic_choices={"embedding.provider": ("e5_onnx",)},  # otra ruta
    )
    # llm.provider NO está mapeado → sigue scalar (texto libre), no enum
    provider = _hijo(_hijo(tree, "llm"), "provider")
    assert provider.field.kind == "scalar"  # type: ignore[union-attr]


def test_dynamic_choices_no_pisa_literal_del_schema():
    """Si la hoja ya es un Literal del schema, dynamic_choices NO la pisa —
    conserva las opciones del Literal (fix del bug scene.provider)."""
    from typing import Literal

    class _Scene(BaseModel):
        provider: Literal["anthropic", "openai", "groq"] = "anthropic"

    tree = build_schema_tree(
        _Scene,
        {"provider": "anthropic"},
        root_label="scene",
        # La misma ruta 'provider' está en dynamic_choices con un set más amplio…
        dynamic_choices={"provider": ("anthropic", "openai", "groq", "ollama", "e5_onnx")},
    )
    provider = _hijo(tree, "provider")
    # …pero el Literal del schema gana: NO se cuelan ollama/e5_onnx.
    assert provider.field.kind == "enum"  # type: ignore[union-attr]
    assert provider.field.enum_choices == ("anthropic", "openai", "groq")  # type: ignore[union-attr]


def test_tristate_gana_sobre_dynamic_choices():
    """Un campo tri-estado NO se convierte en enum aunque su ruta esté en
    dynamic_choices — el tri-estado tiene prioridad (va por su propio modal)."""
    tree = build_schema_tree(
        _Agent,
        {"id": "a", "name": "N", "llm": {"provider": "openai"}},
        root_label="r",
        channel_schemas=_CHANNEL_SCHEMAS,
        tristate_paths=frozenset({"llm.provider"}),
        dynamic_choices={"llm.provider": ("openai", "anthropic")},
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


def test_campo_marcado_secret_se_infiere_kind_secret():
    """End-to-end: un campo con Field(json_schema_extra={'secret':True}) en el
    schema real llega al árbol como kind='secret' (no por su nombre)."""
    from infrastructure.config import TelegramChannelConfig

    tree = build_schema_tree(
        TelegramChannelConfig, {"token": "TKN"}, root_label="telegram"
    )
    token = _hijo(tree, "token")
    assert token.field.kind == "secret"  # type: ignore[union-attr]
    # 'token' ausente → addable marcado is_secret (decide la capa al añadirlo).
    tree2 = build_schema_tree(TelegramChannelConfig, {}, root_label="telegram")
    token_opt = next(o for o in tree2.addable if o.key == "token")
    assert token_opt.is_secret is True


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


# --------------------------------------------------------------------------
# Listas de valores simples → kind="list" (editor de listas, no texto raw)
# --------------------------------------------------------------------------


def test_lista_de_escalares_es_kind_list():
    class _M(BaseModel):
        ids: list[int] = []
        tags: list[str] = []

    tree = build_schema_tree(_M, {"ids": [1, 2], "tags": ["a"]}, root_label="m")
    ids = _hijo(tree, "ids")
    assert ids.field.kind == "list"  # type: ignore[union-attr]
    assert ids.field.list_item_type == "int"  # type: ignore[union-attr]
    assert ids.field.value == [1, 2]  # type: ignore[union-attr]
    tags = _hijo(tree, "tags")
    assert tags.field.kind == "list"  # type: ignore[union-attr]
    assert tags.field.list_item_type == "str"  # type: ignore[union-attr]


def test_lista_de_objetos_no_es_kind_list():
    """list[BaseModel] (ej. knowledge.sources) NO cae a kind=list — es lista de
    OBJETOS, fuera del alcance del editor de listas simples."""

    class _Item(BaseModel):
        x: int = 0

    class _M(BaseModel):
        items: list[_Item] = []

    tree = build_schema_tree(_M, {"items": [{"x": 1}]}, root_label="m")
    assert _hijo(tree, "items").field.kind != "list"  # type: ignore[union-attr]


def test_telegram_allowed_ids_es_lista_editable():
    """End-to-end con el schema real: allowed_user_ids deja de ser texto raw."""
    from infrastructure.config import TelegramChannelConfig

    tree = build_schema_tree(
        TelegramChannelConfig, {"allowed_user_ids": [123, 456]}, root_label="telegram"
    )
    node = _hijo(tree, "allowed_user_ids")
    assert node.field.kind == "list"  # type: ignore[union-attr]
    assert node.field.list_item_type == "int"  # type: ignore[union-attr]
