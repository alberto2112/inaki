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

from core.domain.errors import UnknownTranscriptionProviderError
from core.ports.outbound.transcription_port import ITranscriptionProvider
from infrastructure.config import TranscriptionConfig

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
            module = importlib.import_module(
                f"adapters.outbound.transcription.{module_name}"
            )
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
    def create(cls, cfg: TranscriptionConfig) -> ITranscriptionProvider:
        cls._load()
        provider_name = cfg.provider
        if provider_name not in cls._registry:
            available = list(cls._registry.keys())
            raise UnknownTranscriptionProviderError(
                f"Proveedor de transcripción '{provider_name}' no encontrado. "
                f"Disponibles: {available}"
            )
        return cls._registry[provider_name](cfg)
