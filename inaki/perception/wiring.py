"""Wiring del módulo perception: transcripción (factory por ``PROVIDER_NAME``), fotos y caras.

Único fichero del módulo con permiso para importar ``inaki.config``.

TranscriptionProviderFactory — descubrimiento dinámico de providers de transcripción.

Convención obligatoria para adaptadores en inaki/perception/adapters/transcription/:
- Definir PROVIDER_NAME: str a nivel de módulo
- Definir exactamente una clase que herede de BaseTranscriptionProvider
"""

from __future__ import annotations

import importlib.util

import importlib
import logging
import pkgutil
from pathlib import Path

from collections.abc import Callable
from dataclasses import dataclass

from inaki.config import AgentConfig, PhotosConfig, ProviderConfig, TranscriptionConfig
from inaki.kernel.ports.outbound.tool_port import ITool
from inaki.kernel.ports.outbound.turn_tracer_port import ITurnTracer
from inaki.perception.adapters.face_metadata.sqlite_message_face_metadata_repo import (
    SqliteMessageFaceMetadataRepo,
)
from inaki.perception.adapters.faces.sqlite_face_registry import SqliteFaceRegistryAdapter
from inaki.perception.adapters.imaging.pillow_annotator import PillowPhotoAnnotator
from inaki.perception.adapters.scene.anthropic_describer import AnthropicSceneDescriberAdapter
from inaki.perception.adapters.scene.groq_describer import GroqSceneDescriberAdapter
from inaki.perception.adapters.scene.openai_describer import OpenAISceneDescriberAdapter
from inaki.perception.adapters.transcription.base import (
    BaseTranscriptionProvider,
    ResolvedTranscriptionConfig,
)
from inaki.perception.adapters.vision.insightface_adapter import InsightFaceVisionAdapter
from inaki.perception.ports.face_registry import IFaceRegistryPort
from inaki.perception.ports.scene import ISceneDescriberPort
from inaki.perception.ports.transcription import ITranscriptionProvider
from inaki.perception.ports.vision import IVisionPort
from inaki.perception.settings import PhotosSettings, TranscriptionSettings
from inaki.perception.tools.face_tools import (
    AddPhotoToPersonTool,
    FindDuplicatePersonsTool,
    ForgetPersonTool,
    ListKnownPersonsTool,
    MergePersonsTool,
    RegisterFaceTool,
    SkipFaceTool,
    UpdatePersonMetadataTool,
)
from inaki.perception.use_cases.process_photo import ProcessPhotoUseCase
from inaki.perception.use_cases.transcribe_audio import TranscribeAudioUseCase
from inaki.shared.channel_context import ChannelContext
from inaki.shared.errors import ConfigError, InakiError, UnknownTranscriptionProviderError

logger = logging.getLogger(__name__)


class TranscriptionProviderFactory:
    _registry: dict[str, type] = {}

    @classmethod
    def _load(cls) -> None:
        if cls._registry:
            return

        import inaki.perception.adapters.transcription as transcription_pkg
        from inaki.perception.adapters.transcription.base import BaseTranscriptionProvider

        pkg_path = Path(transcription_pkg.__file__).parent
        for _, module_name, _ in pkgutil.iter_modules([str(pkg_path)]):
            if module_name == "base":
                continue
            module = importlib.import_module(
                f"inaki.perception.adapters.transcription.{module_name}"
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
    def available(cls) -> list[str]:
        """Nombres de los adaptadores de transcripción disponibles (autodescubiertos)."""
        cls._load()
        return sorted(cls._registry)

    @classmethod
    def _resolve_adapter(
        cls, provider_key: str, type_override: str | None
    ) -> type[BaseTranscriptionProvider]:
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


# ---------------------------------------------------------------------------
# Fotos y voz: lo que el composition root pide a este módulo
# ---------------------------------------------------------------------------


def build_photos_settings(photos_cfg: PhotosConfig) -> PhotosSettings:
    return PhotosSettings(
        enabled=photos_cfg.enabled,
        enrollment_chats=photos_cfg.enrollment_chats,
        match_threshold=photos_cfg.faces.match_threshold,
        ambiguous_threshold=photos_cfg.faces.ambiguous_threshold,
    )


def build_transcribe_audio(
    provider: ITranscriptionProvider, transcription_cfg: TranscriptionConfig
) -> TranscribeAudioUseCase:
    """El use case de voz: provider ya resuelto + límites + idioma. Si el agente
    TIENE que tener voz lo decide el canal (composition root), no este módulo."""
    return TranscribeAudioUseCase(
        provider,
        TranscriptionSettings(
            language=transcription_cfg.language,
            max_audio_mb=transcription_cfg.max_audio_mb,
        ),
    )


@dataclass(frozen=True)
class PhotosSingletons:
    """Tier harness-global de fotos: UN modelo de visión y UN registro de caras."""

    vision: IVisionPort
    face_registry: IFaceRegistryPort


def build_photos_singletons(photos_cfg: PhotosConfig, *, faces_db_path: str) -> PhotosSingletons:
    """Lanza si falta ``insightface`` (extra ``faces``): el composition root loguea qué
    capacidad queda muda y arranca sin fotos, en vez de fallar en la primera foto."""
    if importlib.util.find_spec("insightface") is None:
        raise RuntimeError(
            "photos.enabled=true pero insightface no está instalado: pip install 'inaki[faces]'"
        )
    return PhotosSingletons(
        vision=InsightFaceVisionAdapter(photos_cfg.faces.model),
        face_registry=SqliteFaceRegistryAdapter(faces_db_path, embedding_dim=512),
    )


@dataclass(frozen=True)
class PhotosBundle:
    """Lo per-agente de fotos: el use case que consume el canal y las face tools."""

    process_photo: ProcessPhotoUseCase
    tools: list[ITool]


def build_photos_for_agent(
    agent_cfg: AgentConfig,
    photos_cfg: PhotosConfig,
    singletons: PhotosSingletons,
    *,
    get_channel_context: Callable[[], ChannelContext | None],
    tracer: ITurnTracer | None = None,
) -> PhotosBundle:
    """Adapters per-agente (describer de escena, anotador, repo de metadata) + use case + tools.

    Lanza si algo no se puede construir: el composition root decide si degradar
    (hoy: fotos deshabilitadas para ese agente, el resto arranca).
    """
    metadata_repo = SqliteMessageFaceMetadataRepo(agent_cfg.chat_history.db_filename)
    process_photo = ProcessPhotoUseCase(
        vision=singletons.vision,
        face_registry=singletons.face_registry,
        scene_describer=_build_scene_describer(photos_cfg, agent_cfg.providers),
        annotator=PillowPhotoAnnotator(),
        metadata_repo=metadata_repo,
        config=build_photos_settings(photos_cfg),
        tracer=tracer,
    )
    registry = singletons.face_registry
    tools: list[ITool] = [
        RegisterFaceTool(registry, metadata_repo, agent_cfg.id, get_channel_context),
        AddPhotoToPersonTool(registry, metadata_repo, agent_cfg.id, get_channel_context),
        UpdatePersonMetadataTool(registry),
        ListKnownPersonsTool(registry),
        ForgetPersonTool(registry),
        SkipFaceTool(registry, metadata_repo, agent_cfg.id, get_channel_context),
        MergePersonsTool(registry),
        FindDuplicatePersonsTool(registry, photos_cfg.dedup.similarity_threshold),
    ]
    return PhotosBundle(process_photo=process_photo, tools=tools)


def _build_scene_describer(
    photos_cfg: PhotosConfig, providers: dict[str, ProviderConfig]
) -> ISceneDescriberPort:
    """El adaptador de descripción de escena según ``photos.scene.provider``.

    Sin ``scene.api_key`` propia, toma la del registry ``providers`` (por key o
    por ``type``) — el mismo vendor que el LLM no obliga a repetir la credencial.
    """
    scene = photos_cfg.scene
    api_key = scene.api_key
    if not api_key:
        match = providers.get(scene.provider) or next(
            (p for p in providers.values() if p.type == scene.provider), None
        )
        api_key = (match.api_key if match else None) or ""
    if scene.provider == "anthropic":
        return AnthropicSceneDescriberAdapter(api_key, scene.model, scene.prompt_template)
    if scene.provider == "openai":
        return OpenAISceneDescriberAdapter(api_key, scene.model, scene.prompt_template)
    if scene.provider == "groq":
        return GroqSceneDescriberAdapter(api_key, scene.model, scene.prompt_template)
    raise InakiError(
        f"Scene provider desconocido: '{scene.provider}'. Válidos: anthropic, openai, groq"
    )
