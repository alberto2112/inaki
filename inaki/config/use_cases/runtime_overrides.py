"""GestionarOverridesUseCase — aceptar un override de runtime (``config_set``) o rechazarlo.

Es el cerebro de la tool ``config_set``: decide si un cambio se acepta, se compromete
en el store EN MEMORIA y pide el reload que lo aplica. No toca disco, no muta ningún
objeto de config (los providers y los Settings VO reciben sus valores al construirse):
el cambio vive en ``OverridesDeRuntime`` y lo aplica el re-bootstrap del daemon.

Las reglas, en el orden en que se evalúan:

1. **Reload manual pendiente** — descarta todo el store; un ``set`` ahora se perdería.
2. **Denylist** — un path es fijo si el SCHEMA lo dice (``paths_fijos``, inyectado). Se
   compara por igualdad exacta, por descendencia en cualquiera de los dos sentidos
   (pisar un ancestro que CONTIENE algo fijo es tan grave como pisar el fijo) y con
   ``*`` para las claves que pone el operador. Más la red por nombre de hoja de
   ``es_path_secreto`` (``token``, ``password``, ``*_key``).
3. **Path conocido** — contra la config efectiva; si no, sugerencias.
4. **Validación con el loader del arranque** — ``validar`` (inyectado, lo arma el
   wiring) devuelve la config efectiva del agente o levanta. Se corre sobre el store
   ACTUAL (¿carga el disco hoy?) y sobre el candidato (¿carga con el cambio?).
5. **No-op** — si el valor pedido ya es el EFECTIVO (comparado sobre valores
   validados), no se guarda nada ni se pide reload.

El use case no conoce el loader ni los adapters (regla hexagonal): ``validar`` y
``paths_fijos`` llegan inyectados. Todo es SÍNCRONO a propósito: sin ``await`` entre
leer y comprometer, dos ``set`` concurrentes se serializan solos en el event loop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from inaki.config.boundary import errores_del_operador
from inaki.config.ports import ISolicitanteDeReload
from inaki.config.runtime_overrides import (
    AvisoPendiente,
    EntradaOverride,
    OverridesDeRuntime,
    anidar,
)
from inaki.config.use_cases.runtime_config import RuntimeConfigUseCase
from inaki.config.use_cases.show_effective import aplanar, es_path_secreto
from inaki.shared.errors import ConfigError

__all__ = [
    "ConstruccionRechazadaError",
    "EstadoSet",
    "GestionarOverridesUseCase",
    "MotivoRechazo",
    "ResultadoSet",
]


class ConstruccionRechazadaError(ConfigError):
    """Los adapters de LLM/embedding no se pueden construir con esta config.

    Lo levanta el ``validar`` del wiring cuando la etapa 2 (``verificar`` de las
    factories) rechaza lo que el loader aceptó: un provider inexistente o una
    credencial faltante. Es un ``ConfigError`` (el operador lo corrige), pero con
    tipo propio para que el rechazo diga QUÉ etapa falló.
    """


class EstadoSet(str, Enum):
    ACEPTADO = "aceptado"
    SIN_CAMBIO = "sin_cambio"
    RECHAZADO = "rechazado"


class MotivoRechazo(str, Enum):
    RELOAD_MANUAL = "reload_manual"
    PATH_FIJO = "path_fijo"
    PATH_DESCONOCIDO = "path_desconocido"
    CONFIG_INVALIDA = "config_invalida"
    CONSTRUCCION_RECHAZADA = "construccion_rechazada"
    DISCO_ROTO = "disco_roto"


@dataclass(frozen=True)
class ResultadoSet:
    """Veredicto de un ``set``. La tool lo traduce al texto que lee el LLM."""

    estado: EstadoSet
    path: str
    valor: Any = None
    """Valor efectivo tras el cambio (ya validado/coercionado). Solo si no fue rechazado."""
    anterior: Any = None
    """Valor efectivo antes del cambio."""
    motivo: MotivoRechazo | None = None
    detalle: str = ""
    """Qué precisa el motivo: el path fijo, o el mensaje del error de config."""
    sugerencias: tuple[str, ...] = ()


def _coincide(hoja: str, patron: str) -> bool:
    """``hoja`` y ``patron`` se solapan: iguales, o uno cuelga del otro. ``*`` comodín por segmento."""
    a, b = hoja.split("."), patron.split(".")
    return all(p == "*" or p == s for s, p in zip(a, b))


def _bajo(flat: dict[str, Any], path: str) -> dict[str, Any]:
    """Las hojas de ``flat`` que son ``path`` o cuelgan de él."""
    return {k: v for k, v in flat.items() if k == path or k.startswith(f"{path}.")}


class GestionarOverridesUseCase:
    """Acepta o rechaza un ``set`` de runtime para UN agente.

    Args:
        agent_id: El agente dueño de la tool; los campos per-agente se guardan para él.
        overrides: El store en memoria (lo posee el composition root y sobrevive al reload).
        reloader: Quien pide el reload que aplica (Protocol: ``config`` no importa ``app``).
        validar: ``(candidato) -> config efectiva del agente (dict anidado)``. Corre el
            MISMO loader del arranque y la verificación de adapters; levanta
            ``ConfigError``/``ValidationError`` si algo no carga.
        paths_fijos: Patrones que ``config_set`` no puede tocar (``paths_fijos_en_runtime()``).
    """

    def __init__(
        self,
        *,
        agent_id: str,
        overrides: OverridesDeRuntime,
        reloader: ISolicitanteDeReload,
        validar: Callable[[OverridesDeRuntime], dict[str, Any]],
        paths_fijos: frozenset[str],
    ) -> None:
        self._agent_id = agent_id
        self._overrides = overrides
        self._reloader = reloader
        self._validar = validar
        self._paths_fijos = paths_fijos

    def set(
        self,
        path: str,
        valor: Any,
        aviso_a: tuple[str, str] | None = None,
    ) -> ResultadoSet:
        """Valida y, si corresponde, compromete ``path = valor`` y pide el reload.

        Args:
            aviso_a: ``(canal, chat_id)`` del chat que lo pidió, para avisarle cuando el
                daemon vuelva. ``None`` si no hay chat al que avisar (gateway, REST/CLI).
        """
        if self._reloader.modo == "descartar":
            return self._rechazo(path, MotivoRechazo.RELOAD_MANUAL)

        segmentos = path.split(".")
        if not path or any(not s for s in segmentos):
            return self._rechazo(path, MotivoRechazo.PATH_DESCONOCIDO)

        if self._es_fijo(path, valor):
            return self._rechazo(path, MotivoRechazo.PATH_FIJO, detalle=path)

        try:
            actual = self._validar(self._overrides)
        except errores_del_operador() as exc:
            return self._rechazo(path, MotivoRechazo.DISCO_ROTO, detalle=str(exc))

        actual_plano = aplanar(actual)
        if not _bajo(actual_plano, path):
            sugerencias = RuntimeConfigUseCase(actual).sugerencias(path)
            return self._rechazo(path, MotivoRechazo.PATH_DESCONOCIDO, sugerencias=sugerencias)

        entrada = EntradaOverride.para(self._agent_id, path, valor)
        try:
            nuevo = self._validar(self._overrides.con(entrada))
        except ConstruccionRechazadaError as exc:
            return self._rechazo(path, MotivoRechazo.CONSTRUCCION_RECHAZADA, detalle=str(exc))
        except errores_del_operador() as exc:
            return self._rechazo(path, MotivoRechazo.CONFIG_INVALIDA, detalle=str(exc))

        nuevo_plano = aplanar(nuevo)
        anterior = self._valor_en(actual_plano, path)
        efectivo = self._valor_en(nuevo_plano, path)
        if _bajo(actual_plano, path) == _bajo(nuevo_plano, path):
            return ResultadoSet(EstadoSet.SIN_CAMBIO, path, valor=efectivo, anterior=anterior)

        aviso = None
        if aviso_a is not None:
            canal, chat_id = aviso_a
            aviso = AvisoPendiente(
                agent_id=self._agent_id,
                canal=canal,
                chat_id=chat_id,
                texto=(
                    f"Volví con el cambio aplicado: {path} = {efectivo!r}. "
                    "Se pierde con /reload o reinicio."
                ),
            )
        # Síncrono hasta acá: nadie se coló entre `con` y `agregar`.
        self._overrides.agregar(entrada, aviso)
        self._reloader.request_reload_para_aplicar()
        return ResultadoSet(EstadoSet.ACEPTADO, path, valor=efectivo, anterior=anterior)

    # -- internos ------------------------------------------------------------

    def _es_fijo(self, path: str, valor: Any) -> bool:
        """Alguna hoja del valor pedido se solapa con un patrón fijo, o huele a credencial."""
        for hoja in aplanar(anidar(path, valor)):
            if es_path_secreto(hoja, frozenset()):
                return True
            if any(_coincide(hoja, patron) for patron in self._paths_fijos):
                return True
        return False

    @staticmethod
    def _valor_en(plano: dict[str, Any], path: str) -> Any:
        """Valor de la hoja ``path``; si nombra un bloque, el dict de sus hojas relativas."""
        if path in plano:
            return plano[path]
        prefijo = f"{path}."
        return {k[len(prefijo) :]: v for k, v in plano.items() if k.startswith(prefijo)}

    @staticmethod
    def _rechazo(
        path: str,
        motivo: MotivoRechazo,
        *,
        detalle: str = "",
        sugerencias: list[str] | None = None,
    ) -> ResultadoSet:
        return ResultadoSet(
            EstadoSet.RECHAZADO,
            path,
            motivo=motivo,
            detalle=detalle,
            sugerencias=tuple(sugerencias or ()),
        )
