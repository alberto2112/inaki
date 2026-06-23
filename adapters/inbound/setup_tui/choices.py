"""Resolución de *choices* dinámicos POR RUTA para el árbol del setup.

Mapea ciertas rutas del schema (dotted, ej. ``llm.provider``) a su conjunto de
valores elegibles, resuelto contra el ESTADO ACTUAL de la config (providers y
sub-agentes declarados). Alimenta ``build_schema_tree(dynamic_choices=...)`` para
que esos campos se editen ELIGIENDO de una lista en vez de tipear texto libre —
donde el usuario se equivoca y rompe una referencia cruzada.

Por qué acá (y no en el composition root): el mapeo de rutas es conocimiento del
schema que el setup ya introspecciona — vive junto a su par ``_TRISTATE_PATHS``
(ver ``agent_detail_page``). Este módulo NO importa ``infrastructure``: usa el
``IConfigRepository`` que el container ya tiene. El único dato que SÍ viene de
infrastructure —los adaptadores disponibles (catálogo runtime)— se inyecta aparte
como ``SetupContainer.provider_adapters`` y lo consume la página de providers.

Mapeo por RUTA, NO por nombre: ``provider`` aparece en muchas secciones con
dominios distintos (``llm.provider`` = providers declarados; ``photos.scene.provider``
es un ``Literal`` del schema). El mapeo por nombre los pisaba a todos con el mismo
set — bug real. La ruta es precisa y ``_build_leaf`` además respeta los ``Literal``.
"""

from __future__ import annotations

from core.ports.config_repository import IConfigRepository, LayerName

# Rutas (dotted) cuyo valor se elige de los PROVIDERS declarados en ``providers:``.
_PROVIDER_REF_PATHS: tuple[str, ...] = (
    "llm.provider",
    "embedding.provider",
    "transcription.provider",
)

# Rutas (dotted) cuyo valor se elige de los SUB-AGENTES declarados. Son escalares
# (un solo agente). ``delegation.allowed_targets`` queda FUERA: es una lista y
# necesita un modal multi-select que todavía no existe (fase posterior).
_SUBAGENT_REF_PATHS: tuple[str, ...] = (
    "memories.consolidation.agent_id",
    "memories.reconciliation.agent_id",
)


def resolve_choices(repo: IConfigRepository, datos: dict) -> dict[str, tuple[str, ...]]:
    """Devuelve ``{ruta_dotted: choices}`` contextual al estado actual de la config.

    - ``*.provider`` → keys del bloque ``providers:`` (los GLOBALES, leídos del
      repo, UNIDOS a los del scope ``datos`` — así una página de agente ve tanto
      los providers heredados del global como los propios del agente).
    - ``*.agent_id`` de memoria → sub-agentes declarados (``repo.list_sub_agents``).

    Tolerante a fallos: si una lectura del repo falla, esa fuente queda vacía en
    lugar de romper el render del árbol (la TUI debe abrir aunque la config esté
    a medio configurar).

    Args:
        repo: Repositorio de capas de config (el mismo del ``SetupContainer``).
        datos: Valores de la página actual (capa cruda o efectiva ya mergeada).

    Returns:
        Mapa ruta→choices para inyectar en ``build_schema_tree``.
    """
    try:
        global_providers = set((repo.read_layer(LayerName.GLOBAL).get("providers") or {}).keys())
    except Exception:
        global_providers = set()
    local_providers = set((datos.get("providers") or {}).keys())
    providers = tuple(sorted(global_providers | local_providers))

    try:
        subagents = tuple(repo.list_sub_agents())
    except Exception:
        subagents = ()

    out: dict[str, tuple[str, ...]] = {}
    for ruta in _PROVIDER_REF_PATHS:
        out[ruta] = providers
    for ruta in _SUBAGENT_REF_PATHS:
        out[ruta] = subagents
    return out
