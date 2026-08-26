"""Guard de lo que el composition root deriva del schema para ``inaki config show``.

El use case no conoce el schema (regla hexagonal), así que ``inaki/config_cli.py``
le pasa dos cosas ya resueltas: los defaults y qué campos son credenciales. Si
esa extracción se desincroniza del schema, el dump miente — o peor, filtra.
"""

from __future__ import annotations

from inaki.config_cli import _defaults_del_schema, _paths_secretos


def test_los_defaults_traen_lo_que_el_runtime_usa_sin_yaml() -> None:
    defaults = _defaults_del_schema()

    # Bloques con default en el schema: sin ellos el dump mostraría solo lo escrito.
    assert defaults["llm"]["temperature"] == 0.7
    assert defaults["llm"]["timeout_seconds"] == 60
    assert defaults["tools"]["semantic_routing_top_k"] > 0


def test_los_defaults_no_inventan_bloques_opcionales() -> None:
    """``transcription``/``photos`` son ``None`` por default: no son "config efectiva"."""
    defaults = _defaults_del_schema()

    assert "transcription" not in defaults
    assert "photos" not in defaults


def test_se_detectan_las_credenciales_declaradas_en_el_schema() -> None:
    paths = _paths_secretos()

    assert "admin.auth_key" in paths
    assert "channels.telegram.token" in paths
    assert "channels.telegram.broadcast.auth" in paths, "el secreto anidado también cuenta"


def test_los_dicts_indexados_por_el_operador_usan_comodin() -> None:
    """La clave de ``providers`` la elige el operador: el schema no la conoce."""
    paths = _paths_secretos()

    assert "providers.*.api_key" in paths


def test_no_se_inventan_paths_que_ningun_yaml_puede_tener() -> None:
    """Descender por un dict indexado daría ``providers.api_key`` — un path falso.

    Si aparece, el dump listaría una credencial pendiente que no existe y el
    operador iría a buscar dónde configurarla.
    """
    paths = _paths_secretos()

    assert "providers.api_key" not in paths
    assert "channels.api_key" not in paths


def test_toda_credencial_del_schema_queda_cubierta() -> None:
    """Barrido: ningún campo marcado ``secret`` puede quedar fuera de la lista.

    Un secreto no cubierto se imprimiría EN CLARO en ``config show``.
    """
    import inspect

    from pydantic import BaseModel

    import infrastructure.config_schema as schema

    paths = _paths_secretos()
    hojas_cubiertas = {p.split(".")[-1] for p in paths}

    faltantes: list[str] = []
    for _, clase in inspect.getmembers(schema, inspect.isclass):
        if not issubclass(clase, BaseModel) or clase.__module__ != schema.__name__:
            continue
        for nombre, field in clase.model_fields.items():
            extra = field.json_schema_extra or {}
            if isinstance(extra, dict) and extra.get("secret"):
                if nombre not in hojas_cubiertas:
                    faltantes.append(f"{clase.__name__}.{nombre}")

    assert not faltantes, f"credenciales que `config show` imprimiría en claro: {faltantes}"
