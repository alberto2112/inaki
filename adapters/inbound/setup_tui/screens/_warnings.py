"""Validación de referencias cruzadas post-save (compartida entre páginas).

Tras guardar un cambio, avisamos al usuario si rompió una referencia global
(ej. ``app.default_agent`` apuntando a un agente inexistente, o
``memories.llm.provider`` a un provider que no existe). NUNCA se desarma el
cambio — solo se notifica. Extraído de ``BasePage`` para que las páginas v3
(``TreeEditorPage``) lo reusen sin heredar de ``BasePage``.
"""

from __future__ import annotations

from typing import Any, Protocol


class _Notifier(Protocol):
    def __call__(
        self, message: str, *, title: str = ..., severity: str = ..., timeout: int = ...
    ): ...


def warn_on_invalid_refs(container: Any, notify: _Notifier) -> None:
    """Valida la config global efectiva y notifica si hay referencias inválidas.

    Args:
        container: ``SetupContainer`` (o stub) con ``get_effective_config``,
            ``global_schema``, ``list_agents`` y ``list_providers``.
        notify: Callable de notificación (típicamente ``app.notify``).
    """
    if container is None:
        return

    from core.domain.errors import ReferenciaInvalidaError

    try:
        from adapters.inbound.setup_tui.validators.cross_refs import validate_global_config

        efectiva = container.get_effective_config.execute()
        cfg = container.global_schema(**efectiva.datos)
        available_agents = container.list_agents.execute()
        available_providers = [p.key for p in container.list_providers.execute()]
        validate_global_config(cfg, available_agents, available_providers)
    except ReferenciaInvalidaError as exc:
        notify(f"⚠ {exc}", title="referencia inválida", severity="warning", timeout=6)
    except Exception as exc:
        notify(
            f"⚠ config inválida tras guardar: {type(exc).__name__}",
            title="advertencia",
            severity="warning",
            timeout=6,
        )
