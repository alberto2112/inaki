"""Base del schema de configuración: tipos de path, base model y helpers.

``RuntimePath`` ancla paths relativos al home de la instancia; ``_ConfigBaseModel``
fija ``use_attribute_docstrings`` + ``extra="forbid"`` para TODO el schema.
"""

from __future__ import annotations

import logging
from difflib import get_close_matches
from pathlib import Path
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    model_validator,
)

from inaki.config.home import get_inaki_home

logger = logging.getLogger(__name__)


def _expand_user_str(v: Any) -> Any:
    """Expand `~` in a string path. Non-strings pass through untouched."""
    if isinstance(v, str):
        return str(Path(v).expanduser())
    return v


def _expand_user_list(v: Any) -> Any:
    """Expand `~` in every string element of a list. Non-lists pass through."""
    if isinstance(v, list):
        return [str(Path(x).expanduser()) if isinstance(x, str) else x for x in v]
    return v


ExpandedPath = Annotated[str, BeforeValidator(_expand_user_str)]
ExpandedPathList = Annotated[list[str], BeforeValidator(_expand_user_list)]


# Valores SQLite especiales que NO deben interpretarse como paths.
_SQLITE_SPECIAL = {":memory:"}


def _resolve_runtime_path(v: Any) -> Any:
    """
    Resuelve un path de runtime contra el home de instancia (`get_inaki_home()`).

    - Valores no-str pasan sin tocar (ya vienen normalizados).
    - Valores especiales de SQLite (`:memory:`) pasan tal cual.
    - Paths absolutos (incluyendo `~/...` tras expansión) se usan tal cual.
    - Paths relativos se anclan bajo el home de instancia (`get_inaki_home()`).
    """
    if not isinstance(v, str):
        return v
    if v in _SQLITE_SPECIAL:
        return v
    p = Path(v).expanduser()
    if p.is_absolute():
        return str(p)
    return str(get_inaki_home() / p)


RuntimePath = Annotated[str, BeforeValidator(_resolve_runtime_path)]


# ---------------------------------------------------------------------------
# Base común de los modelos de configuración
# ---------------------------------------------------------------------------


class _ConfigBaseModel(BaseModel):
    """Base de TODOS los modelos de configuración del schema.

    Activa ``use_attribute_docstrings``: Pydantic captura el docstring que sigue
    a cada campo y lo expone como ``FieldInfo.description``. De este modo la
    ÚNICA fuente de verdad de la documentación de cada parámetro es su docstring
    acá — de ``description`` salen ``config-reference.md``, ``global.example.yaml``
    y cualquier UI de configuración. Sin este flag, los 130+ docstrings del schema
    no llegarían a ninguna herramienta y habría que leer el código para descubrir
    qué se puede configurar.

    Los ``model_config`` propios de las subclases (``extra="forbid"``,
    ``validate_default``, ``strict``...) se MERGEAN con este — no se pierden.

    Caveat de runtime: Pydantic lee la fuente vía ``inspect.getsource`` al
    definir la clase. Funciona con los ``.py`` presentes en disco (deploy actual:
    systemd + código fuente). Si en el futuro se empaqueta SIN fuentes (zipapp,
    solo ``.pyc``), revalidar que las descripciones se sigan poblando.

    Activa también ``extra="forbid"`` para TODO el schema: una clave que el
    modelo no declara es un typo del operador, y tragárselo en silencio hacía
    que el campo pareciera configurado sin estarlo. El validador de abajo se
    adelanta a Pydantic para nombrar la clave y sugerir la que quiso escribir.
    """

    model_config = ConfigDict(use_attribute_docstrings=True, extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _rechazar_claves_desconocidas(cls, data: Any) -> Any:
        """Convierte el "Extra inputs are not permitted" de Pydantic en algo accionable.

        Corre ANTES de la validación para poder nombrar el bloque, la clave
        sobrante y —cuando hay una parecida— el campo que el operador quiso
        escribir. El ``extra="forbid"`` de arriba queda como red: si un camino
        de construcción esquiva este validador, la clave se rechaza igual.
        """
        if not isinstance(data, dict):
            return data

        conocidas = set(cls.model_fields)
        desconocidas = [k for k in data if isinstance(k, str) and k not in conocidas]
        if not desconocidas:
            return data

        detalles = []
        for clave in sorted(desconocidas):
            parecidas = get_close_matches(clave, conocidas, n=1, cutoff=0.6)
            sugerencia = f" ¿Quisiste decir '{parecidas[0]}'?" if parecidas else ""
            detalles.append(f"'{clave}'{sugerencia}")

        validas = ", ".join(sorted(conocidas)) or "(ninguna)"
        raise ValueError(
            f"{cls.__name__}: clave(s) desconocida(s): {'; '.join(detalles)}. "
            f"Claves válidas: {validas}."
        )
