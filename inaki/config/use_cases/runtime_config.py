"""RuntimeConfigUseCase — la config que el proceso tiene CARGADA, no la escrita.

Hermano de ``ShowEffectiveConfigUseCase``, y la diferencia entre los dos es el
punto entero de este módulo:

- ``ShowEffectiveConfigUseCase`` mergea los YAML **desde disco**. Responde
  "¿qué dice mi configuración?". Es la vista de la CLI, donde el operador
  acaba de editar un fichero y quiere ver el resultado del merge.
- ``RuntimeConfigUseCase`` sirve el snapshot **ya validado que el proceso
  usa**. Responde "¿con qué estoy corriendo AHORA?". Es la vista del agente,
  que no puede contestar por un fichero que quizá nadie recargó.

Las dos divergen en cuanto alguien edita un YAML sin recargar el daemon, y
también —siempre— en los campos que el schema COERCE al validar: un
``~/.inaki/ext`` escrito se carga como ruta absoluta, un ``timezone`` vacío se
resuelve a la zona del sistema. Por eso el valor sale del snapshot en memoria y
no del merge de disco: es el único que no puede mentirle al agente sobre sí
mismo.

El origen de cada valor (``default`` / ``global`` / ``agent``) sí viene de la
vista de disco, capturado en el MISMO instante del arranque en que se construyó
el snapshot — momento en el que disco y memoria coinciden por construcción.
Los valores pisados por ``config_set`` (la capa de overrides en memoria) llevan
el origen ``runtime``, que gana sobre el de disco: es un overlay de una línea
sobre ese mapa, no un segundo merge.

**Los secretos se redactan al construir, no al consultar.** El snapshot que
guarda esta clase ya no contiene ninguna credencial en claro: no hay bandera
que las revele porque no están. Es deliberado — lo que una tool devuelve viaja
al contexto del LLM, al historial y al chat, y eso vale también en un turno de
operador: ni el operador ve una credencial por chat.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import get_close_matches
from typing import Any

from inaki.config.use_cases.show_effective import (
    REDACTADO,
    SIN_CONFIGURAR,
    aplanar,
    es_path_secreto,
)

ORIGEN_RUNTIME = "runtime"
"""Origen de un valor pisado en memoria por ``config_set``. Se pierde en un
``/reload`` manual, un reinicio del servicio o un reload fallido."""

ORIGEN_DESCONOCIDO = "desconocido"
"""Origen de un valor cuyo mapa de capas no se pudo construir.

Preferimos decir "no sé de dónde sale" antes que afirmar ``default``: un origen
inventado es indistinguible de uno real justo cuando se está diagnosticando.
"""


@dataclass(frozen=True)
class CampoRuntime:
    """Un parámetro tal como el proceso lo tiene cargado."""

    path: str
    """Ruta punteada. Ej: ``llm.model``."""

    valor: Any
    """Valor en memoria. Redactado si el campo es una credencial."""

    origen: str
    """Capa que aportó el valor: ``default`` | ``global`` | ``agent`` | ``runtime`` | ``desconocido``."""

    es_secreto: bool = False
    """Si el schema lo marca como credencial."""


class RuntimeConfigUseCase:
    """Consulta el snapshot de config del proceso: un parámetro o un subárbol.

    Args:
        config_en_memoria: Config efectiva YA VALIDADA del agente, anidada
            (típicamente ``global_config`` ⊕ ``agent_config`` dumpeados). Es la
            fuente de los VALORES.
        origenes: Mapa ``path -> capa`` de la vista de disco al arrancar. Los
            paths ausentes quedan en ``ORIGEN_DESCONOCIDO``.
        paths_secretos: Rutas marcadas como credenciales en el schema, con
            comodín ``*`` por segmento (``providers.*.api_key``).
        paths_runtime: Hojas pisadas por ``config_set`` (la capa de overrides en
            memoria). Un path igual a uno de ellos, o debajo de uno, lleva
            ``origen="runtime"``, que pisa el de disco. El VALOR no cambia de
            fuente: ``config_en_memoria`` ya trae los overrides aplicados.
    """

    def __init__(
        self,
        config_en_memoria: dict[str, Any],
        origenes: dict[str, str] | None = None,
        paths_secretos: frozenset[str] = frozenset(),
        paths_runtime: frozenset[str] = frozenset(),
    ) -> None:
        origenes = origenes or {}
        self._campos: dict[str, CampoRuntime] = {}
        for path, valor in sorted(aplanar(config_en_memoria).items()):
            origen = (
                ORIGEN_RUNTIME
                if _es_runtime(path, paths_runtime)
                else origenes.get(path, ORIGEN_DESCONOCIDO)
            )
            self._campos[path] = self._construir(path, valor, origen, paths_secretos)

    @staticmethod
    def _construir(
        path: str, valor: Any, origen: str, paths_secretos: frozenset[str]
    ) -> CampoRuntime:
        if not es_path_secreto(path, paths_secretos):
            return CampoRuntime(path=path, valor=valor, origen=origen)

        # El valor real se descarta acá y nunca entra al snapshot. Lo que sí se
        # conserva es lo único que se puede contestar sin filtrar: si está
        # puesto o si falta.
        configurado = valor not in (None, "", [], {})
        return CampoRuntime(
            path=path,
            valor=REDACTADO if configurado else SIN_CONFIGURAR,
            origen=origen,
            es_secreto=True,
        )

    def get(self, path: str) -> CampoRuntime | None:
        """El parámetro en ``path``, o ``None`` si no existe en la config.

        ``None`` significa "ese parámetro no existe", no "no lo encontré": el
        snapshot incluye los defaults del schema, así que todo campo declarado
        está presente aunque nadie lo haya escrito en un YAML.
        """
        return self._campos.get(path)

    def listar(self, prefix: str | None = None) -> list[CampoRuntime]:
        """Los parámetros bajo ``prefix`` (o todos si es ``None``), ordenados.

        Un ``prefix`` que nombra una hoja (``llm.model``) devuelve ese único
        campo: pedir un subárbol de algo que no lo es no es un error.
        """
        if not prefix:
            return list(self._campos.values())
        return [
            campo
            for path, campo in self._campos.items()
            if path == prefix or path.startswith(f"{prefix}.")
        ]

    def sugerencias(self, path: str, limite: int = 5) -> list[str]:
        """Paths parecidos a ``path``. Para que un fallo diga por dónde seguir.

        Primero los que comparten prefijo (el caso real: el LLM pidió
        ``llm.modelo`` o se quedó en ``llm``), y si no hay, los tipográficamente
        cercanos.
        """
        prefijo = path.rsplit(".", 1)[0] if "." in path else path
        hermanos = [p for p in self._campos if p.startswith(f"{prefijo}.")]
        if hermanos:
            return sorted(hermanos)[:limite]
        return get_close_matches(path, list(self._campos), n=limite, cutoff=0.5)


def _es_runtime(path: str, paths_runtime: frozenset[str]) -> bool:
    """``path`` es una hoja pisada en runtime, o cuelga de una (``a.b`` bajo ``a.b``)."""
    return any(path == p or path.startswith(f"{p}.") for p in paths_runtime)
