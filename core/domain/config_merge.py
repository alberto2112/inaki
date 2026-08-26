"""Motor ÚNICO de merge de capas de configuración.

Antes de este módulo convivían cinco mecanismos con semánticas parecidas pero
no idénticas, y ninguno sabía de los otros:

| Mecanismo | Dónde vivía | Qué sabía hacer de más / de menos |
|---|---|---|
| ``_deep_merge`` | ``infrastructure/config_loader.py`` | carril de carga; NO sabía borrar claves |
| ``_deep_merge`` | ``core/use_cases/config/get_effective_config.py`` | copia literal del anterior |
| ``deep_merge_con_eliminaciones`` | ``core/use_cases/config/_merge.py`` | carril de edición; SÍ borra vía sentinel |
| ``resolve_inherit`` | ``infrastructure/config_loader.py`` | herencia opt-in por bloque |
| ``build_ephemeral_child`` | ``infrastructure/container.py`` | 5ª capa en runtime, invisible a toda UI |

Que "ausente" y "borrado" se expresaran distinto según el carril fue lo que
obligó al setup TUI a inventar un tri-estado propio para poder decir "borrá esta
clave": el carril de carga no tenía forma de expresarlo.

## Dirección del merge — la invariante que no se negocia

``global.yaml`` es la **base**; cada capa siguiente **completa o pisa** los
campos que declara, y solo esos. El orden es siempre base → override, nunca al
revés, y lo mismo vale para el builder de sub-agentes efímeros (el padre es
base, el hijo pisa).

## Semántica — tabla única

| Caso | Resultado |
|---|---|
| dict ⊕ dict | merge recursivo (la clave ausente en override se hereda) |
| clave ausente en override | hereda el valor de base |
| lista ⊕ lista | **reemplazo total** — nunca concatena ni mergea por índice |
| ``None`` explícito | pisa (es "desactivar", no "ausente") |
| ``SENTINEL_ELIMINAR`` | borra la clave del resultado |
| escalar ⊕ dict (o al revés) | **error ruidoso** — antes era reemplazo silencioso |

El footgun de las listas es real y conocido: ``knowledge.sources`` es una lista
de dicts, así que una capa que la redefina pierde TODAS las fuentes de la capa
anterior — no hay merge por ``id``. Está documentado, no resuelto: resolverlo
pide una clave de identidad por tipo de lista, y hoy ninguna la declara.

Este módulo es dominio puro: sin I/O, sin YAML, sin pydantic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from core.domain.errors import ConfigError

__all__ = [
    "SENTINEL_ELIMINAR",
    "Capa",
    "ConfigMergeada",
    "deep_merge",
    "merge_capas",
    "resolver_inherit",
]


class _SentinelEliminar:
    """Marca "borrá esta clave", distinta de "no la toques" y de ``None``."""

    _instancia: "_SentinelEliminar | None" = None

    def __new__(cls) -> "_SentinelEliminar":
        if cls._instancia is None:
            cls._instancia = super().__new__(cls)
        return cls._instancia

    def __repr__(self) -> str:
        return "<SENTINEL_ELIMINAR>"

    def __bool__(self) -> bool:
        return False


SENTINEL_ELIMINAR = _SentinelEliminar()
"""Sentinel de borrado. Un ``None`` significa "escribí null"; esto significa
"sacá la clave", que es lo que hace que el valor vuelva a heredarse."""


def _es_conflicto_de_forma(previo: Any, nuevo: Any) -> bool:
    """``True`` si ``nuevo`` reemplaza a ``previo`` cambiando de forma.

    ``None`` nunca es conflicto: apagar un bloque (``transcription: null``) o
    encenderlo desde nada son operaciones legítimas y explícitas.
    """
    if previo is None or nuevo is None:
        return False
    return isinstance(previo, dict) != isinstance(nuevo, dict)


def _ruta(path: tuple[str, ...]) -> str:
    return ".".join(path) if path else "(raíz)"


def deep_merge(
    base: dict,
    override: dict,
    *,
    _path: tuple[str, ...] = (),
) -> dict:
    """Mergea ``override`` sobre ``base`` según la tabla de semántica del módulo.

    No muta los argumentos. Ver el docstring del módulo para el detalle de cada
    caso; el resumen operativo es: los dicts se funden, todo lo demás se pisa,
    el sentinel borra, y cambiar la forma de una clave entre capas es un error.

    Raises:
        ConfigError: si una clave pasa de escalar a dict (o al revés) entre capas.
    """
    resultado = dict(base)
    for clave, valor in override.items():
        sub_path = _path + (str(clave),)

        if valor is SENTINEL_ELIMINAR:
            resultado.pop(clave, None)
            continue

        previo = resultado.get(clave)
        if isinstance(previo, dict) and isinstance(valor, dict):
            resultado[clave] = deep_merge(previo, valor, _path=sub_path)
            continue

        if clave in resultado and _es_conflicto_de_forma(previo, valor):
            esperado = "un bloque de config (mapa)" if isinstance(previo, dict) else "un valor"
            recibido = "un bloque de config (mapa)" if isinstance(valor, dict) else "un valor"
            raise ConfigError(
                f"Conflicto de tipos en '{_ruta(sub_path)}': una capa declara {esperado} "
                f"y otra {recibido}. Una clave no puede cambiar de forma entre capas — "
                f"revisá cuál de las dos está mal escrita."
            )

        resultado[clave] = valor
    return resultado


@dataclass(frozen=True)
class Capa:
    """Una capa con nombre, para poder decir DE DÓNDE salió cada valor."""

    nombre: str
    datos: dict


@dataclass(frozen=True)
class ConfigMergeada:
    """Resultado del merge con la procedencia de cada hoja.

    ``procedencia`` mapea el path punteado de cada hoja al nombre de la capa que
    la aportó (``"llm.model"`` → ``"agent"``). Es lo que permite responder
    "¿de dónde sale este valor?" sin volver a abrir los ficheros.
    """

    datos: dict
    procedencia: dict[str, str]


def _registrar_procedencia(
    valor: Any,
    path: tuple[str, ...],
    capa: str,
    destino: dict[str, str],
) -> None:
    """Anota ``capa`` como origen de cada hoja bajo ``valor``."""
    if isinstance(valor, dict) and valor:
        for clave, sub in valor.items():
            _registrar_procedencia(sub, path + (str(clave),), capa, destino)
    else:
        # Las hojas incluyen los dicts VACÍOS: declarar `groups: {}` es una
        # decisión del operador y tiene origen, aunque no tenga contenido.
        destino[_ruta(path)] = capa


def _podar_procedencia(destino: dict[str, str], path: tuple[str, ...]) -> None:
    """Olvida la procedencia de un subárbol borrado por el sentinel."""
    prefijo = _ruta(path)
    for clave in [k for k in destino if k == prefijo or k.startswith(prefijo + ".")]:
        del destino[clave]


def merge_capas(capas: Sequence[Capa] | Iterable[Capa]) -> ConfigMergeada:
    """Mergea las capas EN ORDEN (la primera es la base) y rastrea el origen.

    El orden importa y es siempre el mismo que el del sistema: ``global.yaml``
    primero, después las capas que completan o pisan.

    Raises:
        ConfigError: si una clave cambia de forma entre capas (con el nombre de
            la capa que introdujo el conflicto).
    """
    datos: dict = {}
    procedencia: dict[str, str] = {}

    for capa in capas:
        if not capa.datos:
            continue
        try:
            datos = deep_merge(datos, capa.datos)
        except ConfigError as exc:
            raise ConfigError(f"{exc} (al aplicar la capa '{capa.nombre}')") from exc
        _rastrear_capa(capa, datos, procedencia)

    return ConfigMergeada(datos=datos, procedencia=procedencia)


def _rastrear_capa(capa: Capa, datos: dict, procedencia: dict[str, str]) -> None:
    """Actualiza ``procedencia`` con lo que aportó ``capa``, ya mergeado."""

    def _recorrer(nodo: Any, path: tuple[str, ...]) -> None:
        if nodo is SENTINEL_ELIMINAR:
            _podar_procedencia(procedencia, path)
            return
        if isinstance(nodo, dict) and nodo:
            for clave, sub in nodo.items():
                _recorrer(sub, path + (str(clave),))
            return
        # Solo anotamos lo que realmente sobrevivió al merge: una clave que el
        # sentinel borró en la misma capa no tiene procedencia que registrar.
        if _existe_en(datos, path):
            _registrar_procedencia(nodo, path, capa.nombre, procedencia)

    _recorrer(capa.datos, ())


def _existe_en(datos: dict, path: tuple[str, ...]) -> bool:
    nodo: Any = datos
    for clave in path:
        if not isinstance(nodo, dict) or clave not in nodo:
            return False
        nodo = nodo[clave]
    return True


def resolver_inherit(child_raw: dict, parent_raw: dict) -> dict:
    """Resuelve el primitivo ``inherit`` por bloque top-level.

    Herencia **opt-in y por bloque**: cada bloque de ``child_raw`` que sea un
    dict con ``inherit: True`` se resuelve como ``deep_merge(bloque_del_padre,
    bloque_del_hijo)`` — el padre como base, el hijo pisando encima, igual que
    cualquier otra capa. Los bloques sin ``inherit`` (o con ``inherit: False``)
    quedan tal cual vinieron.

    La clave ``inherit`` SIEMPRE se strippea del resultado: es una instrucción
    de merge, no un dato de dominio, así que nunca debe llegar a un modelo.
    """
    resultado: dict = {}
    for clave, valor in child_raw.items():
        if not isinstance(valor, dict) or "inherit" not in valor:
            resultado[clave] = valor
            continue

        bloque_hijo = {k: v for k, v in valor.items() if k != "inherit"}
        if valor.get("inherit") is not True:
            resultado[clave] = bloque_hijo
            continue

        bloque_padre = parent_raw.get(clave)
        if not isinstance(bloque_padre, dict):
            bloque_padre = {}
        resultado[clave] = deep_merge(bloque_padre, bloque_hijo, _path=(str(clave),))
    return resultado
