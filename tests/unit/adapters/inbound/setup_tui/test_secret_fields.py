"""Guard de los campos secretos del schema.

La verdad de "qué campo es secreto" es el marcador explícito
``Field(json_schema_extra={"secret": True})`` (leído por ``_field_is_secret``),
NO el nombre. La heurística por nombre se conserva SOLO acá como red de
seguridad: si alguien agrega un campo con nombre de secreto y se olvida de
marcarlo, este test falla — antes ese campo se mostraría en claro en el setup.
"""

from __future__ import annotations

import inspect
from typing import get_args

from pydantic import BaseModel

from adapters.inbound.setup_tui._schema import (
    _field_is_secret,
    _name_suggests_secret,
    _unwrap_optional,
)
from infrastructure.config import (
    AgentConfig,
    GlobalConfig,
    ProviderConfig,
    TelegramChannelConfig,
)


def _basemodels_in(annotation: object) -> list[type[BaseModel]]:
    """Modelos alcanzables desde una anotación: directa o dentro de dict/list."""
    sub = _unwrap_optional(annotation)
    if inspect.isclass(sub) and issubclass(sub, BaseModel):
        return [sub]
    return [a for a in get_args(sub) if inspect.isclass(a) and issubclass(a, BaseModel)]


def _walk_fields(model: type[BaseModel], seen: set[type[BaseModel]]):
    """DFS por todos los campos de ``model`` y sus sub-modelos (incl. en dict/list)."""
    if model in seen:
        return
    seen.add(model)
    for name, field_info in model.model_fields.items():
        yield model, name, field_info
        for sub in _basemodels_in(field_info.annotation):
            yield from _walk_fields(sub, seen)


def test_todo_campo_con_nombre_de_secreto_esta_marcado():
    """Red de seguridad: ningún campo con nombre sospechoso queda sin marcar."""
    seen: set[type[BaseModel]] = set()
    faltantes: list[str] = []
    # TelegramChannelConfig se agrega aparte: AgentConfig.channels es dict[str,dict]
    # genérico, así que el walk no llega ahí solo.
    for root in (GlobalConfig, AgentConfig, TelegramChannelConfig):
        for model, name, field_info in _walk_fields(root, seen):
            if _name_suggests_secret(name) and not _field_is_secret(field_info):
                faltantes.append(f"{model.__name__}.{name}")

    assert not faltantes, (
        "Campos con nombre de secreto SIN marcar Field(json_schema_extra="
        f"{{'secret': True}}): {faltantes}. Marcalos o el setup los mostrará en claro."
    )


def test_field_is_secret_lee_el_marcador():
    assert _field_is_secret(ProviderConfig.model_fields["api_key"]) is True
    assert _field_is_secret(ProviderConfig.model_fields["base_url"]) is False
    assert _field_is_secret(TelegramChannelConfig.model_fields["token"]) is True


def test_field_is_secret_none_es_false():
    assert _field_is_secret(None) is False


# --------------------------------------------------------------------------
# iter_declared_secrets — la SecretsPage proactiva (paso 1.5)
# --------------------------------------------------------------------------


def _secrets(values):
    from adapters.inbound.setup_tui._schema_tree import iter_declared_secrets

    return {
        ".".join(p): ok
        for p, ok, _ in iter_declared_secrets(
            AgentConfig, values, channel_schemas={"telegram": TelegramChannelConfig}
        )
    }


def test_secreto_configurado_vs_pendiente():
    res = _secrets(
        {
            "id": "a",
            "name": "N",
            "channels": {"telegram": {"token": "TKN"}},
            "providers": {"openai": {"type": "openai"}, "groq": {"api_key": "K"}},
        }
    )
    assert res["channels.telegram.token"] is True  # configurado
    assert res["providers.openai.api_key"] is False  # pendiente (sin api_key)
    assert res["providers.groq.api_key"] is True  # configurado


def test_subseccion_ausente_no_genera_ruido():
    """broadcast NO configurado → su auth NO aparece (evita pendientes de features
    no usadas). Al activar broadcast, sí aparece."""
    sin = _secrets({"id": "a", "name": "N", "channels": {"telegram": {"token": "T"}}})
    assert not any("broadcast" in k for k in sin)

    con = _secrets(
        {"id": "a", "name": "N", "channels": {"telegram": {"token": "T", "broadcast": {"port": 9}}}}
    )
    assert con["channels.telegram.broadcast.auth"] is False


def test_token_vacio_cuenta_como_pendiente():
    res = _secrets({"id": "a", "name": "N", "channels": {"telegram": {"token": ""}}})
    assert res["channels.telegram.token"] is False
