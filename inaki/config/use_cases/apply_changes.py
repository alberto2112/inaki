"""ApplyConfigChangesUseCase — editar una capa y dejar en disco SOLO lo que arranca.

Es el carril de escritura de cualquier UI de config: recibe la capa (global,
agente o sub-agente), cambios por path punteado y paths a HEREDAR (tri-estado
``INHERIT``: borra la clave para que la capa previa vuelva a mandar), aplica
con los mismos use cases de edición por capa, y **valida con un callable
inyectado** que el composition root arma con el loader del arranque. Si el
loader rechaza, restaura el snapshot de la capa y devuelve el mensaje
accionable: nunca queda escrito un YAML que el daemon no pueda cargar.

El use case no conoce el loader ni el schema (regla hexagonal): solo sabe que
``validar()`` levanta cuando la config no carga.

La política "escribir, validar, deshacer" vive UNA vez, en ``escritura_validada``:
la comparten este use case y los que crean o borran agentes y providers
(``manage_agents``, ``manage_providers``). Un carril de escritura sin ella es
un carril que puede dejar en disco algo que no arranca.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from inaki.config.ports import LayerName
from inaki.config.use_cases.intent import CampoTriestado, TristadoValor
from inaki.config.use_cases.update_agent_layer import UpdateAgentLayerUseCase
from inaki.config.use_cases.update_global_layer import UpdateGlobalLayerUseCase

if TYPE_CHECKING:
    from inaki.config.ports import IConfigRepository


class ConfigInvalidaError(Exception):
    """La edición se deshizo porque el loader rechazó el resultado. ``str(exc)`` es el mensaje."""


@contextmanager
def escritura_validada(
    repo: IConfigRepository,
    validar: Callable[[], None],
    capa: LayerName,
    agent_id: str | None = None,
) -> Iterator[None]:
    """Snapshot de la capa → mutación → ``validar()``; si rechaza, la capa vuelve a como estaba.

    Si la capa NO existía antes (crear un agente), deshacer es borrarla: no
    queda ni el fichero recién nacido. Si existía, se reescribe el snapshot.
    """
    existia = repo.layer_exists(capa, agent_id=agent_id)
    snapshot = repo.read_layer(capa, agent_id=agent_id) if existia else None
    yield
    try:
        validar()
    except Exception as exc:
        if existia:
            repo.write_layer(capa, snapshot or {}, agent_id=agent_id)
        else:
            repo.delete_layer(capa, agent_id=agent_id)
        raise ConfigInvalidaError(str(exc)) from exc


@dataclass(frozen=True)
class ResultadoAplicar:
    capa: LayerName
    agent_id: str | None
    cambiados: list[str]
    heredados: list[str]


def _anidar(cambios: dict[str, Any], heredar: list[str]) -> dict[str, Any]:
    """``{"llm.model": "x"}`` + ``["llm.temperature"]`` → dict anidado con tri-estados."""
    raiz: dict[str, Any] = {}
    for path, valor in cambios.items():
        _poner(raiz, path, valor)
    for path in heredar:
        _poner(raiz, path, CampoTriestado(TristadoValor.INHERIT))
    return raiz


def _poner(nodo: dict[str, Any], path: str, valor: Any) -> None:
    partes = path.split(".")
    for parte in partes[:-1]:
        siguiente = nodo.get(parte)
        if not isinstance(siguiente, dict):
            siguiente = {}
            nodo[parte] = siguiente
        nodo = siguiente
    nodo[partes[-1]] = valor


class ApplyConfigChangesUseCase:
    def __init__(self, repo: IConfigRepository, validar: Callable[[], None]) -> None:
        self._repo = repo
        self._validar = validar

    def execute(
        self,
        *,
        capa: LayerName,
        cambios: dict[str, Any],
        heredar: list[str] | None = None,
        agent_id: str | None = None,
    ) -> ResultadoAplicar:
        """Aplica y valida; ante rechazo del loader, restaura la capa y lanza ``ConfigInvalidaError``.

        En la capa global "heredar" significa volver al default del schema: borrar
        la clave lo consigue, así que el verbo es el mismo a propósito.

        Raises:
            ValueError: capa de agente sin ``agent_id``, o nada que aplicar.
            ConfigInvalidaError: el loader rechazó el resultado (la capa quedó como estaba).
        """
        heredar = list(heredar or [])
        if not cambios and not heredar:
            raise ValueError("Nada que aplicar: ni cambios ni paths a heredar.")
        if capa is not LayerName.GLOBAL and not agent_id:
            raise ValueError(f"La capa {capa.value} requiere agent_id.")
        anidado = _anidar(cambios, heredar)
        with escritura_validada(self._repo, self._validar, capa, agent_id):
            if capa is LayerName.GLOBAL:
                UpdateGlobalLayerUseCase(self._repo).execute(anidado)
            else:
                assert agent_id is not None
                UpdateAgentLayerUseCase(self._repo).execute(agent_id, anidado, layer=capa)

        return ResultadoAplicar(
            capa=capa, agent_id=agent_id, cambiados=sorted(cambios), heredados=sorted(heredar)
        )
