"""Guard de ``RuntimeConfigUseCase`` — la config que el proceso tiene cargada.

Tres garantías, y ninguna se negocia:

1. **El valor sale de memoria, no del disco.** Es la razón de existir de esta
   vista frente a ``ShowEffectiveConfigUseCase``: entre lo escrito y lo cargado
   hay un reinicio, y el schema además coerce valores al validar.
2. **Nunca filtra una credencial.** El secreto se descarta al CONSTRUIR: no
   está en el snapshot, así que no hay bandera ni bug que pueda revelarlo.
3. **Sabe decir "no sé".** Un path inexistente devuelve ``None`` y nombra
   vecinos, en vez de dejar que quien pregunta invente el valor que falta.
"""

from __future__ import annotations

from core.use_cases.config.runtime_config import (
    ORIGEN_DESCONOCIDO,
    REDACTADO,
    SIN_CONFIGURAR,
    RuntimeConfigUseCase,
)

_SECRETOS = frozenset({"admin.auth_key", "providers.*.api_key"})


def _uc(memoria: dict, origenes: dict[str, str] | None = None) -> RuntimeConfigUseCase:
    return RuntimeConfigUseCase(
        config_en_memoria=memoria,
        origenes=origenes,
        paths_secretos=_SECRETOS,
    )


def test_el_valor_es_el_de_memoria_aunque_el_origen_venga_del_disco() -> None:
    """El snapshot manda sobre el mapa de capas.

    Es el caso de la coerción del schema: en disco está escrito ``~/.inaki/ext``
    y en memoria vive ya resuelto. La tool tiene que contestar lo segundo — es
    lo que el proceso usa.
    """
    uc = _uc({"app": {"ext_dirs": "/home/pi/.inaki/ext"}}, {"app.ext_dirs": "global"})

    campo = uc.get("app.ext_dirs")

    assert campo is not None
    assert campo.valor == "/home/pi/.inaki/ext"
    assert campo.origen == "global"


def test_un_secreto_con_valor_sale_redactado() -> None:
    uc = _uc({"providers": {"openai": {"api_key": "sk-real"}}})

    campo = uc.get("providers.openai.api_key")

    assert campo is not None
    assert campo.es_secreto
    assert campo.valor == REDACTADO


def test_un_secreto_vacio_se_reporta_como_pendiente() -> None:
    uc = _uc({"admin": {"auth_key": ""}})

    campo = uc.get("admin.auth_key")

    assert campo is not None
    assert campo.valor == SIN_CONFIGURAR


def test_el_valor_real_del_secreto_no_queda_en_ningun_lado() -> None:
    """La redacción es al construir, no al consultar.

    Si el valor real sobreviviera en el snapshot, cualquier bug futuro que
    formatee de más lo filtraría. Barremos TODOS los campos, no solo el
    consultado.
    """
    uc = _uc({"providers": {"openai": {"api_key": "sk-no-debe-aparecer"}}})

    todos = repr([(c.path, c.valor) for c in uc.listar()])

    assert "sk-no-debe-aparecer" not in todos


def test_un_campo_que_huele_a_credencial_se_redacta_sin_estar_en_el_schema() -> None:
    """La red por nombre: ``token`` no está en ``_SECRETOS`` y se redacta igual.

    Cubre la config con shapes viejos o claves en el nivel equivocado, que es
    justo cuando alguien pregunta por su config.
    """
    uc = _uc({"channels": {"telegram": {"token": "123:ABC"}}})

    campo = uc.get("channels.telegram.token")

    assert campo is not None
    assert campo.es_secreto
    assert campo.valor == REDACTADO


def test_sin_mapa_de_origenes_el_campo_dice_desconocido_y_no_inventa_default() -> None:
    uc = _uc({"llm": {"model": "gpt-4o"}}, origenes={})

    campo = uc.get("llm.model")

    assert campo is not None
    assert campo.origen == ORIGEN_DESCONOCIDO


def test_un_path_inexistente_devuelve_none() -> None:
    uc = _uc({"llm": {"model": "gpt-4o"}})

    assert uc.get("llm.modelo") is None


def test_listar_por_prefijo_trae_el_subarbol_y_nada_mas() -> None:
    uc = _uc({"llm": {"model": "x", "temperature": 0.5}, "tools": {"pinned": []}})

    paths = [c.path for c in uc.listar("llm")]

    assert paths == ["llm.model", "llm.temperature"]


def test_listar_un_prefijo_que_es_hoja_devuelve_ese_campo() -> None:
    """Pedir el subárbol de algo que no lo es no es un error."""
    uc = _uc({"llm": {"model": "x", "temperature": 0.5}})

    assert [c.path for c in uc.listar("llm.model")] == ["llm.model"]


def test_listar_un_prefijo_que_no_existe_devuelve_vacio() -> None:
    uc = _uc({"llm": {"model": "x"}})

    assert uc.listar("inventado") == []


def test_listar_sin_prefijo_trae_todo_ordenado() -> None:
    uc = _uc({"tools": {"pinned": []}, "llm": {"model": "x"}})

    assert [c.path for c in uc.listar()] == ["llm.model", "tools.pinned"]


def test_sugerencias_prioriza_los_hermanos_del_path_pedido() -> None:
    uc = _uc({"llm": {"model": "x", "temperature": 0.5}, "memories": {"db_filename": "m.db"}})

    assert uc.sugerencias("llm.modelo") == ["llm.model", "llm.temperature"]


def test_sugerencias_cae_a_parecido_tipografico_cuando_no_hay_hermanos() -> None:
    uc = _uc({"memories": {"db_filename": "m.db"}})

    assert "memories.db_filename" in uc.sugerencias("memories")


def test_un_dict_vacio_es_una_hoja_y_se_reporta() -> None:
    """Declarar ``groups: {}`` es una decisión del operador y tiene que verse."""
    uc = _uc({"channels": {"groups": {}}})

    campo = uc.get("channels.groups")

    assert campo is not None
    assert campo.valor == {}
