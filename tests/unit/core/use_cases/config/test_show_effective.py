"""Guard de ``ShowEffectiveConfigUseCase`` — la config efectiva con su origen.

Dos garantías que no se negocian:

1. **Nunca filtra una credencial.** El output está pensado para pegarse en un
   issue; un secreto sale redactado aunque tenga valor.
2. **Responde qué falta.** Un secreto declarado por el schema y todavía vacío se
   reporta como pendiente — es la vista transversal de credenciales que se
   perdió al erradicar la ``SecretsPage``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from core.ports.config_repository import LayerName
from core.use_cases.config.show_effective import (
    REDACTADO,
    SIN_CONFIGURAR,
    ShowEffectiveConfigUseCase,
)

_SECRETOS = frozenset({"admin.auth_key", "providers.*.api_key", "channels.telegram.token"})


def _repo(global_data: dict, agent_data: dict | None = None) -> MagicMock:
    repo = MagicMock()

    def _read(layer: LayerName, agent_id: str | None = None) -> dict:
        if layer is LayerName.GLOBAL:
            return global_data
        return agent_data or {}

    repo.read_layer.side_effect = _read
    return repo


def _uc(
    global_data: dict, agent_data: dict | None = None, defaults: dict[str, Any] | None = None
) -> ShowEffectiveConfigUseCase:
    return ShowEffectiveConfigUseCase(
        repo=_repo(global_data, agent_data),
        defaults=defaults or {},
        paths_secretos=_SECRETOS,
    )


def _por_path(vista: Any) -> dict[str, Any]:
    return {c.path: c for c in vista.campos}


# ---------------------------------------------------------------------------
# Origen de cada valor
# ---------------------------------------------------------------------------


def test_el_default_del_schema_aparece_aunque_no_este_en_ningun_yaml() -> None:
    """Sin esta capa el dump mostraría lo escrito, no lo que el runtime usa."""
    vista = _uc({}, defaults={"llm": {"temperature": 0.7}}).execute()

    campo = _por_path(vista)["llm.temperature"]
    assert campo.valor == 0.7
    assert campo.origen == "default"


def test_global_pisa_al_default() -> None:
    vista = _uc({"llm": {"temperature": 0.3}}, defaults={"llm": {"temperature": 0.7}}).execute()

    campo = _por_path(vista)["llm.temperature"]
    assert campo.valor == 0.3
    assert campo.origen == "global"


def test_el_agente_pisa_al_global_y_lo_no_declarado_se_hereda() -> None:
    vista = _uc(
        {"llm": {"provider": "groq", "model": "base"}},
        {"llm": {"model": "propio"}},
    ).execute("dev")

    campos = _por_path(vista)
    assert (campos["llm.model"].valor, campos["llm.model"].origen) == ("propio", "agent")
    assert (campos["llm.provider"].valor, campos["llm.provider"].origen) == ("groq", "global")


def test_sin_agente_no_se_lee_la_capa_de_agente() -> None:
    uc = _uc({"app": {"name": "I"}}, {"app": {"name": "NO"}})

    vista = uc.execute()

    assert _por_path(vista)["app.name"].valor == "I"


# ---------------------------------------------------------------------------
# Redacción — nunca filtra
# ---------------------------------------------------------------------------


def test_un_secreto_con_valor_sale_redactado() -> None:
    vista = _uc({"admin": {"auth_key": "clave-real-123"}}).execute()

    campo = _por_path(vista)["admin.auth_key"]
    assert campo.valor == REDACTADO
    assert campo.es_secreto and campo.configurado


def test_ningun_valor_secreto_aparece_en_el_dump() -> None:
    """Barrido: el valor real no debe estar en NINGÚN campo del resultado."""
    vista = _uc(
        {
            "admin": {"auth_key": "clave-real-123"},
            "providers": {"groq": {"api_key": "gsk-secreta"}},
        },
        {"channels": {"telegram": {"token": "123:TOKEN"}}},
    ).execute("dev")

    volcado = " ".join(str(c.valor) for c in vista.campos)
    for secreto in ("clave-real-123", "gsk-secreta", "123:TOKEN"):
        assert secreto not in volcado


def test_el_comodin_cubre_los_dicts_indexados_por_el_operador() -> None:
    """``providers.<clave>.api_key``: la clave la pone el operador, no el schema."""
    vista = _uc({"providers": {"groq": {"api_key": "x"}, "openai": {"api_key": "y"}}}).execute()

    campos = _por_path(vista)
    assert campos["providers.groq.api_key"].valor == REDACTADO
    assert campos["providers.openai.api_key"].valor == REDACTADO


def test_un_campo_no_secreto_del_mismo_bloque_se_muestra_entero() -> None:
    vista = _uc({"providers": {"groq": {"api_key": "x", "base_url": "https://api.groq.com"}}})

    campos = _por_path(vista.execute())
    assert campos["providers.groq.base_url"].valor == "https://api.groq.com"


# ---------------------------------------------------------------------------
# Vista transversal — qué credencial falta
# ---------------------------------------------------------------------------


def test_un_secreto_declarado_pero_vacio_se_reporta_pendiente() -> None:
    vista = _uc({"providers": {"openai": {}}}).execute()

    campo = _por_path(vista)["providers.openai.api_key"]
    assert campo.valor == SIN_CONFIGURAR
    assert campo.es_secreto and not campo.configurado


def test_no_se_reportan_pendientes_de_secciones_no_declaradas() -> None:
    """Listar el token de un canal que nadie configuró es ruido, no información."""
    vista = _uc({"app": {"name": "I"}}).execute()

    assert "channels.telegram.token" not in _por_path(vista)


def test_secretos_filtra_solo_las_credenciales() -> None:
    vista = _uc(
        {"app": {"name": "I"}, "admin": {"auth_key": "k"}, "providers": {"groq": {}}}
    ).execute()

    paths = {c.path for c in vista.secretos()}
    assert paths == {"admin.auth_key", "providers.groq.api_key"}


def test_la_vista_transversal_distingue_puesto_de_pendiente() -> None:
    vista = _uc({"admin": {"auth_key": "k"}, "providers": {"groq": {}}}).execute()

    estado = {c.path: c.configurado for c in vista.secretos()}
    assert estado == {"admin.auth_key": True, "providers.groq.api_key": False}


# ---------------------------------------------------------------------------
# Forma del resultado
# ---------------------------------------------------------------------------


def test_los_campos_salen_ordenados_por_path() -> None:
    vista = _uc({"zeta": {"b": 1}, "alfa": {"a": 2}}).execute()

    paths = [c.path for c in vista.campos]
    assert paths == sorted(paths)


def test_un_bloque_declarado_vacio_es_una_hoja_visible() -> None:
    """``groups: {}`` es una decisión del operador y tiene que verse."""
    vista = _uc({"channels": {"telegram": {"groups": {}}}}).execute()

    assert _por_path(vista)["channels.telegram.groups"].valor == {}


def test_agente_inexistente_lanza_error_en_vez_de_vista_global() -> None:
    """Un id con typo devolvía la vista global-only en silencio: diagnóstico
    convincente y equivocado."""
    import pytest

    from core.domain.errors import AgentNotFoundError

    repo = _repo({"app": {"name": "I"}})
    repo.layer_exists.return_value = False
    uc = ShowEffectiveConfigUseCase(repo=repo, defaults={}, paths_secretos=_SECRETOS)

    with pytest.raises(AgentNotFoundError, match="fantasma"):
        uc.execute("fantasma")


def test_un_sub_agente_tambien_se_puede_mostrar() -> None:
    from core.ports.config_repository import LayerName

    repo = _repo({"llm": {"model": "base"}}, {"llm": {"model": "del-sub"}})
    repo.layer_exists.side_effect = lambda layer, agent_id=None: layer is LayerName.SUB_AGENT
    uc = ShowEffectiveConfigUseCase(repo=repo, defaults={}, paths_secretos=_SECRETOS)

    vista = uc.execute("memory_extractor")

    assert _por_path(vista)["llm.model"].valor == "del-sub"


def test_la_red_por_nombre_redacta_credenciales_fuera_del_schema() -> None:
    """`config show` es la herramienta de diagnóstico cuando el arranque aborta:
    justo ahí la config trae shapes viejos o bloques varados que los paths del
    schema no cubren. Sin la red por nombre, esas credenciales saldrían en claro
    en el momento de pegar el output en un issue."""
    vista = _uc(
        {
            "llm": {"api_key": "sk-LEGACY"},
            "tool_config": {"gsn": {"password": "PLANO", "username": "alberto"}},
            "servicio": {"access_token": "tok-X"},
        }
    ).execute()

    campos = _por_path(vista)
    assert campos["llm.api_key"].valor == REDACTADO
    assert campos["tool_config.gsn.password"].valor == REDACTADO
    assert campos["servicio.access_token"].valor == REDACTADO
    assert campos["tool_config.gsn.username"].valor == "alberto", "no sobre-redactar"
