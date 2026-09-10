"""ManageProvidersUseCase — el registry ``providers:`` desde una UI, validado y sin huérfanos.

Envuelve ``UpsertProviderUseCase`` y ``DeleteProviderUseCase`` en
``escritura_validada`` (capa global). Guard propio, porque el loader NO lo
vigila: no se borra un provider al que apunte ``llm.provider``,
``embedding.provider``, ``transcription.provider`` o ``memories.llm.provider``
en global NI en ninguna capa de agente. Sin él, el borrado pasa la validación
y el daemon muere al ensamblar ese agente.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from inaki.config.ports import LayerName
from inaki.config.use_cases.apply_changes import escritura_validada
from inaki.config.use_cases.delete_provider import DeleteProviderUseCase
from inaki.config.use_cases.list_providers import ListProvidersUseCase, ProviderInfo
from inaki.config.use_cases.manage_agents import EntidadEnUsoError
from inaki.config.use_cases.upsert_provider import UpsertProviderUseCase

if TYPE_CHECKING:
    from inaki.config.ports import IConfigRepository

# Paths (punteados) que referencian una entrada del registry por su key.
PATHS_QUE_REFERENCIAN = (
    "llm.provider",
    "embedding.provider",
    "transcription.provider",
    "memories.llm.provider",
)


def _leer(datos: dict[str, Any], path: str) -> Any:
    nodo: Any = datos
    for seg in path.split("."):
        if not isinstance(nodo, dict):
            return None
        nodo = nodo.get(seg)
    return nodo


class ManageProvidersUseCase:
    def __init__(self, repo: IConfigRepository, validar: Callable[[], None]) -> None:
        self._repo = repo
        self._validar = validar

    def listar(self) -> list[ProviderInfo]:
        return ListProvidersUseCase(self._repo).execute()

    def guardar(
        self,
        key: str,
        *,
        type: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        """Crea o edita ``providers.<key>``; ``api_key`` vacía o ``None`` no toca la existente."""
        if not key or "." in key or "/" in key:
            raise ValueError(f"Key de provider inválida: {key!r}")
        with escritura_validada(self._repo, self._validar, LayerName.GLOBAL):
            UpsertProviderUseCase(self._repo).execute(
                key, type=type, base_url=base_url, api_key=api_key or None
            )

    def borrar(self, key: str) -> None:
        """Borra ``providers.<key>`` (credencial incluida) salvo que alguien lo referencie.

        Raises:
            EntidadEnUsoError: alguna capa lo referencia; el mensaje dice cuál y por qué path.
            ConfigInvalidaError: el loader rechazó el resultado (capa restaurada).
        """
        usos = self.referencias(key)
        if usos:
            detalle = ", ".join(f"{capa}:{path}" for capa, path in usos)
            raise EntidadEnUsoError(
                f"El provider '{key}' está en uso ({detalle}). Cambiá esas referencias antes."
            )
        with escritura_validada(self._repo, self._validar, LayerName.GLOBAL):
            DeleteProviderUseCase(self._repo).execute(key)

    def referencias(self, key: str) -> list[tuple[str, str]]:
        """``[(capa, path)]`` de todo lo que apunta a ``key``, en global y en cada agente."""
        capas: list[tuple[str, dict[str, Any]]] = [
            ("global", self._repo.read_layer(LayerName.GLOBAL))
        ]
        capas += [
            (a, self._repo.read_layer(LayerName.AGENT, agent_id=a))
            for a in self._repo.list_agents()
        ]
        capas += [
            (s, self._repo.read_layer(LayerName.SUB_AGENT, agent_id=s))
            for s in self._repo.list_sub_agents()
        ]
        return [
            (nombre, path)
            for nombre, datos in capas
            for path in PATHS_QUE_REFERENCIAN
            if _leer(datos, path) == key
        ]
