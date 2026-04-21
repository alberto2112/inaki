"""
LLMProviderFactory — descubrimiento dinámico de providers LLM.

Convención obligatoria para adaptadores en adapters/outbound/providers/:
- Definir PROVIDER_NAME: str a nivel de módulo
- Definir exactamente una clase que herede de BaseLLMProvider
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path

from core.ports.outbound.llm_port import ILLMProvider
from infrastructure.config import LLMConfig

logger = logging.getLogger(__name__)


class LLMProviderFactory:
    _registry: dict[str, type] = {}

    @classmethod
    def _load(cls) -> None:
        if cls._registry:
            return

        import adapters.outbound.providers as providers_pkg
        from adapters.outbound.providers.base import BaseLLMProvider

        pkg_path = Path(providers_pkg.__file__).parent
        for _, module_name, _ in pkgutil.iter_modules([str(pkg_path)]):
            if module_name == "base":
                continue
            module = importlib.import_module(f"adapters.outbound.providers.{module_name}")
            provider_name = getattr(module, "PROVIDER_NAME", None)
            if provider_name is None:
                continue
            for attr in vars(module).values():
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseLLMProvider)
                    and attr is not BaseLLMProvider
                ):
                    cls._registry[provider_name] = attr
                    logger.debug("LLM provider registrado: '%s' → %s", provider_name, attr.__name__)
                    break

        logger.info("LLMProviderFactory: providers disponibles: %s", list(cls._registry))

    @classmethod
    def create(cls, cfg) -> ILLMProvider:
        cls._load()
        provider_name = cfg.llm.provider
        if provider_name not in cls._registry:
            available = list(cls._registry.keys())
            raise ValueError(
                f"Proveedor LLM '{provider_name}' no encontrado. Disponibles: {available}"
            )
        return cls._registry[provider_name](cfg.llm)

    @classmethod
    def create_from_llm_config(cls, llm_cfg: LLMConfig) -> ILLMProvider:
        """
        Instancia un ``ILLMProvider`` a partir de una ``LLMConfig`` directa.

        Pensado para casos donde la config viene resuelta de un merge (p. ej.,
        ``MemoryConfig.resolved_llm_config(base)``) y no queremos construir un
        ``AgentConfig`` falso solo para satisfacer la API de ``create(cfg)``.
        """
        cls._load()
        provider_name = llm_cfg.provider
        if provider_name not in cls._registry:
            available = list(cls._registry.keys())
            raise ValueError(
                f"Proveedor LLM '{provider_name}' no encontrado. Disponibles: {available}"
            )
        return cls._registry[provider_name](llm_cfg)
