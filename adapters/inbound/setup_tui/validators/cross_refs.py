"""
Validadores de referencias cruzadas para la config global.

Validan que los campos que apuntan a recursos por nombre (agentes, providers)
no estén rotos — es decir, que el recurso efectivamente exista.

Estas funciones son PURAS: no hacen I/O, no leen YAML ni disco. El caller
les pasa las listas de recursos disponibles ya resueltas.

Ejemplos de referencias cruzadas que pueden estar rotas:
  - ``app.default_agent`` apunta a un agente que no tiene ``.yaml``
  - ``llm.provider`` apunta a un provider no declarado en ``providers:``
  - ``embedding.provider`` apunta a un provider desconocido
"""

from __future__ import annotations

from core.domain.errors import ReferenciaInvalidaError

# ---------------------------------------------------------------------------
# Funciones de validación individuales
# ---------------------------------------------------------------------------


def validate_default_agent_exists(default_agent: str, available: list[str]) -> None:
    """
    Verifica que ``default_agent`` esté en la lista de agentes disponibles.

    Args:
        default_agent: Valor del campo ``app.default_agent`` en la config.
        available: Lista de ids de agentes disponibles (de ``list_agents()``).

    Raises:
        ReferenciaInvalidaError: Si ``default_agent`` no está en ``available``.
    """
    if default_agent not in available:
        raise ReferenciaInvalidaError(
            campo="app.default_agent",
            valor=default_agent,
            disponibles=sorted(available),
        )


def validate_provider_reference(provider_name: str, available: list[str]) -> None:
    """
    Verifica que ``provider_name`` esté en la lista de providers disponibles.

    Args:
        provider_name: Nombre del provider a validar (ej: ``"groq"``, ``"openai"``).
        available: Lista de nombres de providers disponibles (keys del bloque ``providers:``).

    Raises:
        ReferenciaInvalidaError: Si ``provider_name`` no está en ``available``.
    """
    if provider_name not in available:
        raise ReferenciaInvalidaError(
            campo="providers",
            valor=provider_name,
            disponibles=sorted(available),
        )


# ---------------------------------------------------------------------------
# Providers que no necesitan estar en el registry (locales / self-contained)
# ---------------------------------------------------------------------------

_PROVIDERS_LOCALES: frozenset[str] = frozenset({"e5_onnx", "ollama"})
"""
Providers que operan sin credenciales externas y por convención NO requieren
entrada en el bloque ``providers:``. Si el campo ``provider`` de un feature
apunta a uno de estos, la validación de referencia se saltea.
"""


# ---------------------------------------------------------------------------
# Validación de una GlobalConfig completa
# ---------------------------------------------------------------------------


def validate_global_config(
    cfg: object,
    available_agents: list[str],
    available_providers: list[str],
) -> None:
    """
    Valida todas las referencias cruzadas de una ``GlobalConfig``.

    Verifica en orden:
      1. ``app.default_agent`` en ``available_agents``
      2. ``llm.provider`` en ``available_providers`` (salvo locales)
      3. ``embedding.provider`` en ``available_providers`` (salvo locales)
      4. ``transcription.provider`` en ``available_providers`` (si hay bloque)
      5. ``memory.llm.provider`` en ``available_providers`` (si hay override)

    Lanza el PRIMER error encontrado — no acumula errores.

    Args:
        cfg: Una instancia de ``GlobalConfig`` (tipado como ``object`` para
            evitar importar ``infrastructure.config`` desde ``adapters/``).
        available_agents: Lista de ids de agentes disponibles.
        available_providers: Lista de nombres de providers disponibles.

    Raises:
        ReferenciaInvalidaError: Por la primera referencia rota encontrada.
    """
    # 1. default_agent
    default_agent: str = cfg.app.default_agent  # type: ignore[attr-defined]
    validate_default_agent_exists(default_agent, available_agents)

    # 2. llm.provider
    llm_provider: str = cfg.llm.provider  # type: ignore[attr-defined]
    if llm_provider not in _PROVIDERS_LOCALES:
        _validate_provider_campo(
            campo="llm.provider",
            valor=llm_provider,
            available=available_providers,
        )

    # 3. embedding.provider
    emb_provider: str = cfg.embedding.provider  # type: ignore[attr-defined]
    if emb_provider not in _PROVIDERS_LOCALES:
        _validate_provider_campo(
            campo="embedding.provider",
            valor=emb_provider,
            available=available_providers,
        )

    # 4. transcription.provider (opcional)
    transcription = cfg.transcription  # type: ignore[attr-defined]
    if transcription is not None:
        tr_provider: str = transcription.provider
        if tr_provider not in _PROVIDERS_LOCALES:
            _validate_provider_campo(
                campo="transcription.provider",
                valor=tr_provider,
                available=available_providers,
            )

    # 5. memory.llm.provider (override opcional)
    memory = cfg.memory  # type: ignore[attr-defined]
    if memory is not None:
        memory_llm = memory.llm
        if memory_llm is not None and memory_llm.provider is not None:
            mem_llm_provider: str = memory_llm.provider
            if mem_llm_provider not in _PROVIDERS_LOCALES:
                _validate_provider_campo(
                    campo="memory.llm.provider",
                    valor=mem_llm_provider,
                    available=available_providers,
                )

    # 6. photos.scene.provider (opcional — skip si photos no está configurado)
    photos = getattr(cfg, "photos", None)
    if photos is not None:
        scene = getattr(photos, "scene", None)
        if scene is not None:
            scene_provider: str = scene.provider
            # Los providers de escena son remotos — siempre requieren credenciales.
            # No forman parte de _PROVIDERS_LOCALES.
            _validate_provider_campo(
                campo="photos.scene.provider",
                valor=scene_provider,
                available=available_providers,
            )


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _validate_provider_campo(campo: str, valor: str, available: list[str]) -> None:
    """
    Helper interno: lanza ``ReferenciaInvalidaError`` con el ``campo`` exacto.
    """
    if valor not in available:
        raise ReferenciaInvalidaError(
            campo=campo,
            valor=valor,
            disponibles=sorted(available),
        )
