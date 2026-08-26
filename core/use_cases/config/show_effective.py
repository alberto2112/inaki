"""ShowEffectiveConfigUseCase — la config que el runtime realmente ve, con su origen.

Responde la pregunta que hasta ahora obligaba a abrir varios ficheros y hacer el
merge mentalmente: *¿de dónde sale este valor?* Es también la base sobre la que
puede apoyarse cualquier interfaz de configuración: una UI sobre config efectiva
con origen es un problema simple; sobre N ficheros crudos más la semántica de
merge, es el problema que el setup TUI lleva años peleando.

Tres capas, en el orden del sistema:

1. ``default``  — lo que declara el schema (nunca está escrito en un YAML).
2. ``global``   — ``config/global.yaml``.
3. ``agent``    — ``agents/{id}.yaml`` (o el sub-agente).

Los valores marcados como secretos en el schema **se redactan**: el dump sirve
para diagnosticar y para pegar en un issue, así que nunca debe filtrar una
credencial. A cambio informa lo que sí importa de un secreto sin revelarlo:
si está configurado o sigue pendiente — la vista transversal de credenciales
que se perdió al erradicar la ``SecretsPage``.

El use case no conoce el schema (regla hexagonal): el composition root le pasa
los defaults y el set de paths secretos ya resueltos.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.domain.config_merge import Capa, merge_capas

if TYPE_CHECKING:
    from core.ports.config_repository import IConfigRepository

REDACTADO = "********"
"""Marca de un secreto con valor. Nunca se emite el valor real."""

SIN_CONFIGURAR = "(sin configurar)"
"""Marca de un secreto declarado por el schema pero todavía sin valor."""


@dataclass(frozen=True)
class CampoEfectivo:
    """Un valor de la config efectiva, con su procedencia."""

    path: str
    """Ruta punteada del campo. Ej: ``llm.model``."""

    valor: Any
    """Valor efectivo. Redactado si el campo es secreto."""

    origen: str
    """Capa que aportó el valor: ``default`` | ``global`` | ``agent``."""

    es_secreto: bool = False
    """Si el schema lo marca como credencial (``kind == "secret"``)."""

    configurado: bool = True
    """Solo informativo para secretos: ``False`` si no tiene valor todavía."""


@dataclass(frozen=True)
class ConfigConOrigen:
    """La config efectiva completa, campo por campo y ordenada por path."""

    agent_id: str | None
    campos: list[CampoEfectivo]

    def secretos(self) -> list[CampoEfectivo]:
        """Solo los campos marcados como credenciales.

        Es la vista transversal "qué tengo configurado y qué me falta" sin
        tener que navegar el árbol entero.
        """
        return [c for c in self.campos if c.es_secreto]


def _aplanar(nodo: Any, path: tuple[str, ...] = ()) -> dict[str, Any]:
    """Aplana un dict anidado a ``{"a.b.c": valor}``.

    Un dict VACÍO es una hoja: declarar ``groups: {}`` es una decisión del
    operador y tiene que verse en el dump.
    """
    if isinstance(nodo, dict) and nodo:
        salida: dict[str, Any] = {}
        for clave, sub in nodo.items():
            salida.update(_aplanar(sub, path + (str(clave),)))
        return salida
    return {".".join(path): nodo}


class ShowEffectiveConfigUseCase:
    """Devuelve la config efectiva de un agente con el origen de cada valor.

    Args:
        repo: Repositorio de capas YAML.
        defaults: Config que el schema aplica cuando nadie declara nada. La
            resuelve el composition root — el core no conoce el schema.
        paths_secretos: Rutas punteadas marcadas como credenciales. Soporta
            comodín por segmento con ``*`` para los dicts indexados por nombre
            (``providers.*.api_key``, ``channels.*.token``).
    """

    def __init__(
        self,
        repo: "IConfigRepository",
        defaults: dict[str, Any] | None = None,
        paths_secretos: frozenset[str] = frozenset(),
    ) -> None:
        self._repo = repo
        self._defaults = defaults or {}
        self._paths_secretos = paths_secretos

    def execute(self, agent_id: str | None = None) -> ConfigConOrigen:
        """Arma la config efectiva. ``agent_id=None`` → solo la capa global."""
        from core.ports.config_repository import LayerName

        capas = [Capa("default", self._defaults)]
        capas.append(Capa("global", self._repo.read_layer(LayerName.GLOBAL)))
        if agent_id is not None:
            capas.append(Capa("agent", self._repo.read_layer(LayerName.AGENT, agent_id=agent_id)))

        resultado = merge_capas(capas)
        plano = _aplanar(resultado.datos)
        self._agregar_secretos_pendientes(plano, resultado.datos)

        campos = [
            self._construir_campo(path, valor, resultado.procedencia.get(path, "default"))
            for path, valor in sorted(plano.items())
        ]
        return ConfigConOrigen(agent_id=agent_id, campos=campos)

    def _agregar_secretos_pendientes(self, plano: dict[str, Any], datos: dict) -> None:
        """Suma los secretos que el schema declara y el YAML todavía no tiene.

        Sin esto, un ``providers.openai: {}`` sin ``api_key`` no aparecería en
        ningún lado y la pregunta "¿qué credencial me falta?" quedaría sin
        responder — que es justo lo que hacía la ``SecretsPage``.

        Solo se reportan pendientes de secciones que el operador YA declaró:
        listar el ``auth`` de un broadcast que nadie configuró convierte la
        vista en ruido de features no usadas (mismo criterio que tenía la
        ``SecretsPage``).
        """
        for patron in self._paths_secretos:
            if "*" not in patron:
                if patron not in plano and self._seccion_declarada(datos, patron):
                    plano[patron] = None
                continue

            prefijo, _, resto = patron.partition(".*.")
            contenedor = self._navegar(datos, prefijo.split("."))
            if not isinstance(contenedor, dict):
                continue
            for clave in contenedor:
                path = f"{prefijo}.{clave}.{resto}"
                if path not in plano:
                    plano[path] = None

    @classmethod
    def _seccion_declarada(cls, datos: dict, patron: str) -> bool:
        """``True`` si el bloque que contiene a ``patron`` existe en la config."""
        padre = patron.split(".")[:-1]
        return not padre or isinstance(cls._navegar(datos, padre), dict)

    @staticmethod
    def _navegar(datos: dict, segmentos: list[str]) -> Any:
        nodo: Any = datos
        for seg in segmentos:
            if not isinstance(nodo, dict) or seg not in nodo:
                return None
            nodo = nodo[seg]
        return nodo

    def _construir_campo(self, path: str, valor: Any, origen: str) -> CampoEfectivo:
        if not self._es_secreto(path):
            return CampoEfectivo(path=path, valor=valor, origen=origen)

        configurado = valor not in (None, "", [], {})
        return CampoEfectivo(
            path=path,
            valor=REDACTADO if configurado else SIN_CONFIGURAR,
            origen=origen,
            es_secreto=True,
            configurado=configurado,
        )

    def _es_secreto(self, path: str) -> bool:
        """``True`` si ``path`` matchea alguna ruta secreta, con ``*`` por segmento."""
        segmentos = path.split(".")
        for patron in self._paths_secretos:
            partes = patron.split(".")
            if len(partes) != len(segmentos):
                continue
            if all(p == "*" or p == s for p, s in zip(partes, segmentos)):
                return True
        return False
