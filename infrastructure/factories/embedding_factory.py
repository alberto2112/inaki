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

from core.ports.outbound.embedding_port import IEmbeddingProvider

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
    def create(cls, cfg) -> IEmbeddingProvider:
        cls._load()
        provider_name = cfg.embedding.provider
        if provider_name not in cls._registry:
            available = list(cls._registry.keys())
            raise ValueError(
                f"Proveedor embedding '{provider_name}' no encontrado. Disponibles: {available}"
            )
        return cls._registry[provider_name](cfg.embedding)
