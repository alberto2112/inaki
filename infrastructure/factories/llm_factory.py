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

from core.domain.errors import ConfigError
from core.ports.outbound.llm_port import ILLMProvider
from infrastructure.config import LLMConfig, ProviderConfig, ResolvedLLMConfig

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
    def _resolve_adapter(cls, provider_key: str, type_override: str | None) -> type:
        """Resuelve la clase del adapter a partir del ``type`` (o la key si no hay type)."""
        cls._load()
        type_key = type_override or provider_key
        if type_key not in cls._registry:
            available = list(cls._registry.keys())
            raise ValueError(f"Proveedor LLM '{type_key}' no encontrado. Disponibles: {available}")
        return cls._registry[type_key]

    @classmethod
    def create(
        cls,
        llm_cfg: LLMConfig,
        providers: dict[str, ProviderConfig],
    ) -> ILLMProvider:
        """
        Construye un ``ILLMProvider`` a partir de la ``LLMConfig`` del agente y
        el registry top-level de proveedores.

        Flujo:
          1. Resuelve la entrada del registry bajo la key ``llm_cfg.provider``.
          2. Deriva el ``type`` (explícito o igual a la key).
          3. Si el adapter requiere creds y no hay entrada, levanta ``ConfigError``.
          4. Compone ``ResolvedLLMConfig`` (feature + provider) y lo inyecta.
        """
        provider_key = llm_cfg.provider
        provider_cfg = providers.get(provider_key)
        adapter_type = cls._resolve_adapter(
            provider_key, provider_cfg.type if provider_cfg else None
        )

        if provider_cfg is None:
            if adapter_type.REQUIRES_CREDENTIALS:
                raise ConfigError(
                    f"Provider '{provider_key}' requiere credenciales pero no existe la "
                    f"entrada 'providers.{provider_key}' en la configuración."
                )
            provider_cfg = ProviderConfig()

        resolved = ResolvedLLMConfig(
            provider=provider_key,
            model=llm_cfg.model,
            temperature=llm_cfg.temperature,
            max_tokens=llm_cfg.max_tokens,
            reasoning_effort=llm_cfg.reasoning_effort,
            timeout_seconds=llm_cfg.timeout_seconds,
            api_key=provider_cfg.api_key,
            base_url=provider_cfg.base_url,
        )
        return adapter_type(resolved)

    @classmethod
    def create_from_resolved(cls, resolved: ResolvedLLMConfig) -> ILLMProvider:
        """
        Instancia un ``ILLMProvider`` a partir de un ``ResolvedLLMConfig`` ya
        compuesto (p. ej. por ``MemoryConfig.resolved_llm_config``).

        Valida ``REQUIRES_CREDENTIALS`` contra el adapter para fail-fast cuando
        el provider referenciado por un override no existe en el registry.
        """
        adapter_type = cls._resolve_adapter(resolved.provider, None)
        if adapter_type.REQUIRES_CREDENTIALS and not resolved.api_key:
            raise ConfigError(
                f"Provider '{resolved.provider}' requiere credenciales pero no "
                f"existe la entrada 'providers.{resolved.provider}' con api_key."
            )
        return adapter_type(resolved)
