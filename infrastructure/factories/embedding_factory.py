"""
EmbeddingProviderFactory — descubrimiento dinámico de providers de embedding.

Convención obligatoria para adaptadores en adapters/outbound/embedding/:
- Definir PROVIDER_NAME: str a nivel de módulo
- Definir exactamente una clase que herede de BaseEmbeddingProvider
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path

from core.domain.errors import ConfigError
from core.ports.outbound.embedding_port import IEmbeddingProvider
from infrastructure.config import EmbeddingConfig, ProviderConfig, ResolvedEmbeddingConfig

logger = logging.getLogger(__name__)


class EmbeddingProviderFactory:
    _registry: dict[str, type] = {}

    @classmethod
    def _load(cls) -> None:
        if cls._registry:
            return

        import adapters.outbound.embedding as embedding_pkg
        from adapters.outbound.embedding.base import BaseEmbeddingProvider

        pkg_path = Path(embedding_pkg.__file__).parent
        for _, module_name, _ in pkgutil.iter_modules([str(pkg_path)]):
            if module_name == "base":
                continue
            module = importlib.import_module(f"adapters.outbound.embedding.{module_name}")
            provider_name = getattr(module, "PROVIDER_NAME", None)
            if provider_name is None:
                continue
            for attr in vars(module).values():
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseEmbeddingProvider)
                    and attr is not BaseEmbeddingProvider
                ):
                    cls._registry[provider_name] = attr
                    logger.debug(
                        "Embedding provider registrado: '%s' → %s",
                        provider_name,
                        attr.__name__,
                    )
                    break

        logger.info(
            "EmbeddingProviderFactory: providers disponibles: %s",
            list(cls._registry),
        )

    @classmethod
    def _resolve_adapter(cls, provider_key: str, type_override: str | None) -> type:
        cls._load()
        type_key = type_override or provider_key
        if type_key not in cls._registry:
            available = list(cls._registry.keys())
            raise ValueError(
                f"Proveedor embedding '{type_key}' no encontrado. Disponibles: {available}"
            )
        return cls._registry[type_key]

    @classmethod
    def create(
        cls,
        embedding_cfg: EmbeddingConfig,
        providers: dict[str, ProviderConfig],
    ) -> IEmbeddingProvider:
        """Construye un ``IEmbeddingProvider`` resolviendo creds desde el registry."""
        provider_key = embedding_cfg.provider
        provider_cfg = providers.get(provider_key)
        adapter_type = cls._resolve_adapter(
            provider_key, provider_cfg.type if provider_cfg else None
        )

        if provider_cfg is None:
            if adapter_type.REQUIRES_CREDENTIALS:
                raise ConfigError(
                    f"Provider embedding '{provider_key}' requiere credenciales "
                    f"pero no existe la entrada 'providers.{provider_key}'."
                )
            provider_cfg = ProviderConfig()

        resolved = ResolvedEmbeddingConfig(
            provider=provider_key,
            model_dirname=embedding_cfg.model_dirname,
            model=embedding_cfg.model,
            dimension=embedding_cfg.dimension,
            cache_filename=embedding_cfg.cache_filename,
            api_key=provider_cfg.api_key,
            base_url=provider_cfg.base_url,
        )
        return adapter_type(resolved)
