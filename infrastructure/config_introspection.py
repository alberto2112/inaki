"""Introspección del schema de configuración — lo que el schema SABE de sí mismo.

Deriva del schema Pydantic (``GlobalConfig`` / ``AgentConfig``) las dos cosas
que cualquier vista de config necesita y que ``core/`` no puede conocer:

1. **Los defaults** — lo que el runtime aplica cuando nadie declara nada. Sin
   ellos, una vista solo muestra lo escrito en los YAML y no lo que el sistema
   realmente usa.
2. **Qué campos son credenciales** — las rutas marcadas con ``secret`` en el
   schema, para redactarlas. Un campo es secreto por su marca en el schema, no
   por el fichero donde está escrito.

Vive en ``infrastructure`` porque acá el schema es conocido de primera mano y
porque tiene DOS consumidores, ambos composition roots: la CLI
(``inaki config show``) y el container (la tool ``config`` que el LLM invoca).
Tenerlo en uno de los dos obligaría al otro a importarlo al revés.
"""

from __future__ import annotations

import inspect
from typing import Any

from pydantic import BaseModel


def defaults_del_schema() -> dict[str, Any]:
    """Config que el schema aplica cuando nadie declara nada.

    Es la capa base del dump: sin ella, ``config show`` solo mostraría lo
    escrito en los YAML y no lo que el runtime realmente usa.

    Los bloques marcados como requeridos (``llm``, ``memories``, …) también
    entran: el loader los materializa SIEMPRE con ``Modelo(**merged.get(x, {}))``,
    así que su "obligatoriedad" es estructural y no llega nunca al operador.
    Omitirlos dejaría fuera del dump justamente los campos más consultados.
    """
    from infrastructure.config import AgentConfig, GlobalConfig

    defaults: dict[str, Any] = {}
    for modelo in (GlobalConfig, AgentConfig):
        for nombre, field in modelo.model_fields.items():
            if nombre in defaults:
                continue
            valor = _default_de_campo(field)
            if valor is not None:
                defaults[nombre] = valor
    return defaults


def _default_de_campo(field: Any) -> Any:
    """Default efectivo de un campo, o ``None`` si no tiene uno representable."""
    if not field.is_required():
        valor = field.get_default(call_default_factory=True)
        return valor.model_dump() if isinstance(valor, BaseModel) else valor

    # Requerido: si su tipo es un modelo instanciable sin argumentos, el loader
    # lo construye así. Un requerido escalar (``id``, ``name``) no tiene default.
    anotacion = field.annotation
    if inspect.isclass(anotacion) and issubclass(anotacion, BaseModel):
        try:
            return anotacion().model_dump()
        except Exception:
            return None
    return None


def paths_secretos() -> frozenset[str]:
    """Rutas punteadas de los campos marcados como credenciales en el schema.

    Usa ``*`` para los dicts indexados por nombre (``providers.*.api_key``,
    ``channels.*.token``): esas claves las pone el operador, no el schema.
    """
    from infrastructure.config import CHANNEL_SCHEMAS, AgentConfig, GlobalConfig

    encontrados: set[str] = set()

    def _recorrer(modelo: type[BaseModel], prefijo: str, vistos: set[type]) -> None:
        if modelo in vistos:
            return
        vistos.add(modelo)
        for nombre, field in modelo.model_fields.items():
            extra = field.json_schema_extra or {}
            if isinstance(extra, dict) and extra.get("secret"):
                encontrados.add(f"{prefijo}{nombre}")
            for sub in _submodelos(field.annotation):
                _recorrer(sub, f"{prefijo}{nombre}.", vistos)

    def _submodelos(annotation: Any) -> list[type[BaseModel]]:
        from typing import get_args, get_origin

        # Un dict indexado (``providers: dict[str, ProviderConfig]``) NO se
        # recorre acá: su path real lleva una clave que pone el operador, así
        # que se cubre aparte con comodín. Descender daría ``providers.api_key``,
        # un path que no existe en ningún YAML.
        if get_origin(annotation) is dict:
            return []
        candidatos = [annotation, *get_args(annotation)]
        return [c for c in candidatos if inspect.isclass(c) and issubclass(c, BaseModel)]

    vistos: set[type] = set()
    for raiz in (GlobalConfig, AgentConfig):
        _recorrer(raiz, "", vistos)

    # `providers` y `channels` son dicts indexados por una clave del operador:
    # la recursión por anotaciones no puede nombrarlas, así que van con comodín.
    from infrastructure.config import ProviderConfig

    for nombre, field in ProviderConfig.model_fields.items():
        extra = field.json_schema_extra or {}
        if isinstance(extra, dict) and extra.get("secret"):
            encontrados.add(f"providers.*.{nombre}")

    for canal, modelo in CHANNEL_SCHEMAS.items():
        _recolectar_secretos_anidados(modelo, f"channels.{canal}.", encontrados)

    return frozenset(encontrados)


def _recolectar_secretos_anidados(
    modelo: type[BaseModel], prefijo: str, destino: set[str], vistos: set[type] | None = None
) -> None:
    """Igual que el recorrido de arriba pero arrancando en un modelo de canal."""
    from typing import get_args

    vistos = vistos if vistos is not None else set()
    if modelo in vistos:
        return
    vistos.add(modelo)
    for nombre, field in modelo.model_fields.items():
        extra = field.json_schema_extra or {}
        if isinstance(extra, dict) and extra.get("secret"):
            destino.add(f"{prefijo}{nombre}")
        for candidato in (field.annotation, *get_args(field.annotation)):
            if inspect.isclass(candidato) and issubclass(candidato, BaseModel):
                _recolectar_secretos_anidados(candidato, f"{prefijo}{nombre}.", destino, vistos)
