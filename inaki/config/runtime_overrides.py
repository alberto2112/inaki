"""Overrides de config EN MEMORIA — la capa que ``config_set`` apila sobre los YAML.

Dominio puro del módulo config: sin I/O. El store es una lista ORDENADA de
entradas; cada una es una mini-capa que se materializa con el motor único de
merge (``inaki/config/merge.py``), así que la semántica es la de dos capas YAML:
dict ⊕ dict funde, lista reemplaza, ``None`` pisa, y cambiar de forma entre
entradas es ``ConfigError``. No hay un segundo merge.

El store NUNCA toca disco: vive en el proceso y sobrevive al loop de reload del
daemon (que reconstruye todo lo demás). Un ``/reload`` manual lo vacía
(``descartar_todo``), un reinicio del servicio o un reload fallido lo pierden.
Persistir es editar la capa YAML (``inaki config web``).

Concurrencia: todas las operaciones son síncronas. Sin ``await`` entre ``con`` y
``agregar``, dos ``config_set`` concurrentes se serializan solos en el event loop.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from inaki.config.merge import deep_merge
from inaki.config.use_cases.show_effective import aplanar

__all__ = [
    "AvisoPendiente",
    "Destino",
    "EntradaOverride",
    "OverridesDeRuntime",
    "anidar",
    "destino_de",
]

Destino = Literal["global", "agente"]


def destino_de(path: str) -> Destino:
    """A qué capa va un path: la del agente si el schema del agente declara su bloque raíz.

    Se deriva del schema, no de una lista: ``llm``, ``tools``, ``memories``… los
    declara ``AgentConfig`` (van al agente del turno); ``user``, ``scheduler``,
    ``app``, ``knowledge``… son ``GlobalConfig``-only (afectan a todo el arnés).
    """
    from inaki.config import AgentConfig

    raiz = path.split(".", 1)[0]
    return "agente" if raiz in AgentConfig.model_fields else "global"


def anidar(path: str, valor: Any) -> dict[str, Any]:
    """``("llm.model", "x")`` → ``{"llm": {"model": "x"}}``."""
    nodo: Any = valor
    for clave in reversed(path.split(".")):
        nodo = {clave: nodo}
    return nodo


@dataclass(frozen=True)
class EntradaOverride:
    """Un ``set`` aceptado: un path con su valor, dirigido a una capa.

    ``agent_id`` es el agente dueño de la entrada cuando ``destino == "agente"``
    y ``None`` cuando es global. Mezclar las dos cosas es un error de programa
    (``ValueError``), no de config: el use case deriva el destino con
    ``destino_de`` y construye la entrada con ``para``.
    """

    destino: Destino
    agent_id: str | None
    path: str
    valor: Any

    def __post_init__(self) -> None:
        if any(not segmento for segmento in self.path.split(".")):
            raise ValueError(f"path inválido para un override de runtime: {self.path!r}")
        if self.destino == "agente" and not self.agent_id:
            raise ValueError("una entrada con destino 'agente' necesita agent_id")
        if self.destino == "global" and self.agent_id is not None:
            raise ValueError("una entrada con destino 'global' no lleva agent_id")
        # Copia defensiva: el llamador puede seguir mutando el dict que pasó.
        object.__setattr__(self, "valor", copy.deepcopy(self.valor))

    @classmethod
    def para(cls, agent_id: str, path: str, valor: Any) -> EntradaOverride:
        """Entrada para ``path`` pedida desde el agente ``agent_id``, con destino derivado del schema."""
        destino = destino_de(path)
        return cls(
            destino=destino,
            agent_id=agent_id if destino == "agente" else None,
            path=path,
            valor=valor,
        )


@dataclass(frozen=True)
class AvisoPendiente:
    """Una línea que hay que mandar al chat que pidió el cambio cuando el daemon vuelva."""

    agent_id: str
    canal: str
    chat_id: str
    texto: str


class OverridesDeRuntime:
    """Pila ordenada de overrides en memoria, más los avisos pendientes de entregar."""

    def __init__(self) -> None:
        self._entradas: list[EntradaOverride] = []
        self._avisos: list[AvisoPendiente] = []

    # -- escritura ---------------------------------------------------------

    def con(self, entrada: EntradaOverride) -> OverridesDeRuntime:
        """Copia con ``entrada`` agregada. PURA: sirve para validar un candidato sin comprometerlo."""
        candidato = OverridesDeRuntime()
        candidato._entradas = [*self._entradas, entrada]
        return candidato

    def agregar(self, entrada: EntradaOverride, aviso: AvisoPendiente | None) -> None:
        """Compromete ``entrada`` (y su aviso) tras haberla validado.

        Raises:
            ConfigError: si la entrada cambia de forma contra las ya apiladas. El
                store queda intacto: nunca guarda un estado que no se pueda
                materializar.
        """
        candidato = self.con(entrada)
        candidato.capa_global()
        candidato.capas_por_agente()
        self._entradas = candidato._entradas
        if aviso is not None:
            self._avisos.append(aviso)

    def descartar_todo(self) -> list[str]:
        """Vacía entradas y avisos; devuelve una línea por entrada descartada, en orden.

        Es lo que hace el ``/reload`` manual: el cambio no se aplicó, así que el
        aviso de éxito que esperaba tampoco corresponde.
        """
        lineas = [f"{e.agent_id or 'global'}: {e.path} = {e.valor!r}" for e in self._entradas]
        self._entradas = []
        self._avisos = []
        return lineas

    def tomar_avisos(self) -> list[AvisoPendiente]:
        """Devuelve los avisos pendientes y los vacía (cada uno se entrega una vez)."""
        avisos, self._avisos = self._avisos, []
        return avisos

    # -- lectura: lo que consume el loader ----------------------------------

    def capa_global(self) -> dict[str, Any]:
        """Capa de las entradas ``GlobalConfig``-only, lista para ``load_global_config``."""
        return self._materializar(e for e in self._entradas if e.destino == "global")

    def capa_agente(self, agent_id: str) -> dict[str, Any]:
        """Capa de las entradas del agente ``agent_id`` (``{}`` si no tiene)."""
        return self._materializar(
            e for e in self._entradas if e.destino == "agente" and e.agent_id == agent_id
        )

    def capas_por_agente(self) -> dict[str, dict[str, Any]]:
        """Una capa por cada agente que tiene entradas."""
        ids = dict.fromkeys(
            e.agent_id for e in self._entradas if e.destino == "agente" and e.agent_id
        )
        return {agent_id: self.capa_agente(agent_id) for agent_id in ids}

    def paths_runtime(self, agent_id: str) -> frozenset[str]:
        """Hojas pisadas que ve el agente: las suyas más las globales.

        Es lo que lee la tool ``config`` para marcar el origen ``runtime``.
        """
        capas = (self.capa_global(), self.capa_agente(agent_id))
        # `aplanar({})` devuelve una hoja con clave vacía: una capa sin entradas no aporta nada.
        return frozenset(path for capa in capas if capa for path in aplanar(capa))

    # -- internos ------------------------------------------------------------

    @staticmethod
    def _materializar(entradas: Iterable[EntradaOverride]) -> dict[str, Any]:
        """Apila las entradas en orden con ``deep_merge`` y devuelve una copia profunda.

        ``deep_merge`` pone por referencia el valor de la entrada cuando la clave no
        está en la base, y ``load_global_config`` MUTA el dict mergeado
        (``knowledge_raw.pop("sources")``): sin la copia, cargar vaciaría el store.
        """
        capa: dict[str, Any] = {}
        for entrada in entradas:
            capa = deep_merge(capa, anidar(entrada.path, entrada.valor))
        return copy.deepcopy(capa)
