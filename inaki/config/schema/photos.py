"""Bloque ``photos``: reconocimiento facial, descripción de escena y dedup.

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from inaki.config.schema._base import _ConfigBaseModel

# ---------------------------------------------------------------------------
# GlobalConfig — config del sistema (sin agentes)
# ---------------------------------------------------------------------------


class FacesConfig(_ConfigBaseModel):
    """Configuración del proveedor de reconocimiento facial (InsightFace)."""

    provider: Literal["insightface"] = "insightface"
    """Motor de detección y embedding facial. ``insightface`` es el único soportado.

    DECLARATIVO: al haber una sola opción, ningún componente lo lee — el adapter
    de visión se instancia directo. Existe para que agregar un segundo motor no
    sea un breaking change de config."""

    model: Literal["buffalo_sc", "buffalo_s", "buffalo_l"] = "buffalo_sc"
    """Pack de modelos de InsightFace, de más liviano a más preciso.

    ``buffalo_sc`` (default, ~200MB) es el indicado para la Pi 5; ``buffalo_s`` y
    ``buffalo_l`` ganan precisión a costa de RAM y descarga (``buffalo_l`` ~1GB).
    ⚠ Cambiarlo INVALIDA ``faces.db``: los embeddings guardados son de otro
    espacio vectorial. Hay que parar el daemon, borrar la DB y volver a enrolar
    todas las caras."""

    enrollment_chats: Literal["private", "none"] = "private"
    """Tipos de chat donde el agente ofrece registrar las caras que no reconoce.

    ``private`` = solo en chats privados: ahí anota la foto numerando las caras
    desconocidas o ambiguas y ofrece enrolarlas. ``none`` = nunca lo ofrece (la
    foto se sigue analizando y las caras conocidas se siguen nombrando).
    Hasta 2026-10 era ``photos.enrollment_chats``; el arranque lo migra solo."""

    match_threshold: float = 0.55
    """Score mínimo de similitud coseno para considerar una cara como MATCHED."""
    ambiguous_threshold: float = 0.40
    """Score entre ambiguous_threshold y match_threshold → cara AMBIGUOUS."""

    @model_validator(mode="after")
    def _validar_umbrales(self) -> "FacesConfig":
        if self.ambiguous_threshold >= self.match_threshold:
            raise ValueError(
                f"FacesConfig: ambiguous_threshold ({self.ambiguous_threshold}) "
                f"debe ser menor que match_threshold ({self.match_threshold})."
            )
        return self


class SceneConfig(_ConfigBaseModel):
    """Configuración del proveedor de descripción de escena (LLM multimodal)."""

    provider: Literal["anthropic", "openai", "groq", "deepseek"] = "anthropic"
    """Vendor del LLM multimodal que describe la foto: ``anthropic``, ``openai``, ``groq``
    o ``deepseek``.

    Registry PROPIO, independiente del de ``llm``: cada opción tiene su adapter
    de escena. Si ``api_key`` está vacío, la credencial se busca en
    ``providers:`` por esta misma key (o por una entrada cuyo ``type`` coincida).
    Un valor fuera de los cuatro soportados aborta el arranque del pipeline."""

    model: str = "claude-sonnet-4-6"
    """Modelo multimodal a usar, en el nombre que espera el ``provider`` elegido.

    Tiene que soportar visión: ``claude-sonnet-4-6`` para anthropic, ``gpt-4o``
    para openai, un scout de llama-4 para groq, ``deepseek-flash`` para deepseek."""

    prompt_template: str | None = None
    """Prompt personalizado en español. None = usar el prompt built-in del adaptador."""
    api_key: str | None = Field(default=None, json_schema_extra={"secret": True})
    """API key del proveedor. Conviene referenciar una entrada de ``providers:`` bajo photos.scene.api_key."""


class DedupConfig(_ConfigBaseModel):
    """Configuración del job nocturno de deduplicación de personas."""

    enabled: bool = True
    """Habilita el job nocturno de deduplicación de personas.

    Con ``False`` la tarea builtin no se registra en el scheduler. Requiere
    además ``photos.enabled: true`` y un scheduler activo — el job vive en el
    daemon."""

    schedule: str = "0 3 * * *"
    """Expresión cron para el job de deduplicación. Validada por croniter."""
    similarity_threshold: float = 0.70
    """Score mínimo de similitud coseno entre centroides para reportar par duplicado."""


class PhotosConfig(_ConfigBaseModel):
    """Configuración del pipeline de fotos (reconocimiento facial + escena)."""

    enabled: bool = True
    """Si False, el bot ignora todas las fotos con warning. No se carga ningún modelo."""
    faces: FacesConfig = FacesConfig()
    """Reconocimiento facial local (InsightFace): qué modelo y con qué umbrales decide."""

    scene: SceneConfig = SceneConfig()
    """Descripción de la escena vía LLM multimodal: vendor, modelo, prompt y credencial.

    Es el DEFAULT de todos los agentes: cada ``agents/{id}.yaml`` puede pisarlo campo
    a campo con su propio ``photos.scene``. Es lo único de ``photos`` que se pisa por
    agente: ``faces``, ``dedup`` y ``enabled`` son del arnés."""

    dedup: DedupConfig = DedupConfig()
    """Job nocturno que detecta personas registradas dos veces y reporta los pares."""


class AgentPhotosConfig(_ConfigBaseModel):
    """Lo de ``photos`` que un agente puede pisar: solo la descripción de escena.

    El resto del bloque es del tier harness-global y no tiene campo acá a propósito:
    ``faces`` es UN modelo en RAM y UN ``faces.db`` compartido (dos modelos con la
    misma dimensión de embedding compararían vectores de espacios distintos sin que
    nada lo detecte), ``dedup`` es UN job sobre ese registro, y ``enabled`` decide si
    esos singletons se cargan. Declararlos en ``agents/{id}.yaml`` aborta la carga."""

    scene: SceneConfig = SceneConfig()
    """Escena de ESTE agente: pisa campo a campo ``photos.scene`` del global.

    Útil para que cada agente describa las fotos con su voz (``prompt_template``) o
    con otro vendor/modelo. Lo que no declara lo hereda del global. Sin el pipeline
    de fotos activo en el global (``photos`` ausente o ``enabled: false``) no tiene
    efecto, igual que el resto de ``photos``."""
