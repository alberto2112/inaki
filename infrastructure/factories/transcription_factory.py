"""
TranscriptionProviderFactory — descubrimiento dinámico de providers de transcripción.

Convención obligatoria para adaptadores en adapters/outbound/transcription/:
- Definir PROVIDER_NAME: str a nivel de módulo
- Definir exactamente una clase que herede de BaseTranscriptionProvider
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path

from core.domain.errors import ConfigError, UnknownTranscriptionProviderError
from core.ports.outbound.transcription_port import ITranscriptionProvider
from infrastructure.config import (
    ProviderConfig,
    ResolvedTranscriptionConfig,
    TranscriptionConfig,
)

logger = logging.getLogger(__name__)


class TranscriptionProviderFactory:
    _registry: dict[str, type] = {}

    @classmethod
    def _load(cls) -> None:
        if cls._registry:
            return

        import adapters.outbound.transcription as transcription_pkg
        from adapters.outbound.transcription.base import BaseTranscriptionProvider

        pkg_path = Path(transcription_pkg.__file__).parent
        for _, module_name, _ in pkgutil.iter_modules([str(pkg_path)]):
            if module_name == "base":
                continue
            module = importlib.import_module(f"adapters.outbound.transcription.{module_name}")
            provider_name = getattr(module, "PROVIDER_NAME", None)
            if provider_name is None:
                continue
            for attr in vars(module).values():
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseTranscriptionProvider)
                    and attr is not BaseTranscriptionProvider
                ):
                    cls._registry[provider_name] = attr
                    logger.debug(
                        "Transcription provider registrado: '%s' → %s",
                        provider_name,
                        attr.__name__,
                    )
                    break

        logger.info(
            "TranscriptionProviderFactory: providers disponibles: %s",
            list(cls._registry),
        )

    @classmethod
    def _resolve_adapter(cls, provider_key: str, type_override: str | None) -> type:
        cls._load()
        type_key = type_override or provider_key
        if type_key not in cls._registry:
            available = list(cls._registry.keys())
            raise UnknownTranscriptionProviderError(
                f"Proveedor de transcripción '{type_key}' no encontrado. Disponibles: {available}"
            )
        return cls._registry[type_key]

    @classmethod
    def create(
        cls,
        transcription_cfg: TranscriptionConfig,
        providers: dict[str, ProviderConfig],
    ) -> ITranscriptionProvider:
        """Construye un ``ITranscriptionProvider`` resolviendo creds desde el registry."""
        provider_key = transcription_cfg.provider
        provider_cfg = providers.get(provider_key)
        adapter_type = cls._resolve_adapter(
            provider_key, provider_cfg.type if provider_cfg else None
        )

        if provider_cfg is None:
            if adapter_type.REQUIRES_CREDENTIALS:
                raise ConfigError(
                    f"Provider de transcripción '{provider_key}' requiere credenciales "
                    f"pero no existe la entrada 'providers.{provider_key}'."
                )
            provider_cfg = ProviderConfig()

        resolved = ResolvedTranscriptionConfig(
            provider=provider_key,
            model=transcription_cfg.model,
            language=transcription_cfg.language,
            timeout_seconds=transcription_cfg.timeout_seconds,
            max_audio_mb=transcription_cfg.max_audio_mb,
            api_key=provider_cfg.api_key,
            base_url=provider_cfg.base_url,
        )
        return adapter_type(resolved)
