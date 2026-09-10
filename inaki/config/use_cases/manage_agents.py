"""ManageAgentsUseCase — crear y borrar agentes dejando en disco solo lo que arranca.

Envuelve ``CreateAgentUseCase`` y ``DeleteAgentUseCase`` (que escriben y borran
sin preguntar) en ``escritura_validada``: crear un agente que el loader rechaza
no deja fichero; borrar uno que rompe la carga lo restaura.

Guard propio, porque el loader NO lo vigila: no se borra el agente que
``app.default_agent`` nombra. Sin él, el borrado pasa la validación y el
próximo ``inaki`` muere con un error que no dice por qué.

Lo que se borra es el YAML (credenciales incluidas). Los datos del agente
(``history.db``, memoria, ``users/``) no se tocan: borrar datos no es una
operación de config.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from inaki.config.ports import LayerName
from inaki.config.use_cases.apply_changes import escritura_validada
from inaki.config.use_cases.create_agent import CreateAgentUseCase
from inaki.config.use_cases.delete_agent import DeleteAgentUseCase
from inaki.shared.errors import InakiError

if TYPE_CHECKING:
    from inaki.config.ports import IConfigRepository


class EntidadEnUsoError(InakiError):
    """No se puede borrar: otra parte de la config la referencia. El mensaje dice quién."""


class ManageAgentsUseCase:
    def __init__(self, repo: IConfigRepository, validar: Callable[[], None]) -> None:
        self._repo = repo
        self._validar = validar

    def crear(
        self,
        agent_id: str,
        nombre: str,
        *,
        descripcion: str = "",
        system_prompt: str = "",
        sub_agente: bool = False,
        template_extra: dict[str, Any] | None = None,
    ) -> None:
        """Crea ``agents/{id}.yaml`` (o el sub-agente) y valida; si el loader rechaza, no queda nada.

        Raises:
            AgentYaExisteError: el id ya está ocupado.
            ConfigInvalidaError: el loader rechazó el resultado (fichero borrado).
        """
        capa = LayerName.SUB_AGENT if sub_agente else LayerName.AGENT
        with escritura_validada(self._repo, self._validar, capa, agent_id):
            CreateAgentUseCase(self._repo).execute(
                agent_id,
                nombre,
                descripcion=descripcion,
                system_prompt=system_prompt,
                template_extra=template_extra,
                layer=capa,
            )

    def borrar(self, agent_id: str, *, sub_agente: bool = False) -> None:
        """Borra el YAML del agente y valida; si el loader rechaza, el YAML vuelve.

        Raises:
            AgentNotFoundError: no existe.
            EntidadEnUsoError: es el ``app.default_agent``.
            ConfigInvalidaError: el loader rechazó el resultado (YAML restaurado).
        """
        capa = LayerName.SUB_AGENT if sub_agente else LayerName.AGENT
        if not sub_agente:
            app_cfg = self._repo.read_layer(LayerName.GLOBAL).get("app") or {}
            if app_cfg.get("default_agent") == agent_id:
                raise EntidadEnUsoError(
                    f"'{agent_id}' es el app.default_agent: elegí otro default antes de borrarlo."
                )
        with escritura_validada(self._repo, self._validar, capa, agent_id):
            DeleteAgentUseCase(self._repo).execute(agent_id, layer=capa)
