"""Bloque ``embedding``: vectorizador compartido por routing, memoria y knowledge.

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

from pydantic import ConfigDict
from inaki.config.schema._base import RuntimePath, _ConfigBaseModel


class EmbeddingConfig(_ConfigBaseModel):
    """Modelo de embeddings que alimenta todo el retrieval del sistema.

    Un solo vectorizador sirve a tres consumidores: el semantic routing de tools
    y skills, la búsqueda de memoria y el pre-fetch de knowledge. Bloque
    per-agente, con registry ``providers:`` para las credenciales (el default
    ``e5_onnx`` es local y no necesita ninguna).

    ⚠ Cambiar de modelo (o de ``dimension``) invalida TODOS los vectores ya
    persistidos: no hay auto-migración — hay que borrar y recrear la DB de
    memoria y los índices de knowledge.
    """

    model_config = ConfigDict(validate_default=True)  # RuntimePath en los defaults

    provider: str = "e5_onnx"
    """KEY del registry ``providers:`` que vectoriza. ``e5_onnx`` (local) u ``openai``.

    ``e5_onnx`` corre multilingual-e5-small con ONNX Runtime en la propia
    máquina: sin red, sin costo y sin entrada en ``providers:`` — es lo
    recomendado en la Pi 5. ``openai`` requiere ``providers.openai.api_key``."""

    model_dirname: RuntimePath = "models/e5-small"
    """Directorio con los ficheros del modelo ONNX. SOLO lo usa ``e5_onnx``.

    Espera adentro ``model.onnx`` y ``tokenizer.json`` (descargables de
    HuggingFace: ``intfloat/multilingual-e5-small``). Relativo al home de
    instancia; se reancla con ``--home`` / ``INAKI_HOME``. Un path absoluto se
    usa tal cual."""

    model: str = "text-embedding-3-small"
    """Nombre del modelo remoto. SOLO lo usa el provider ``openai``; ``e5_onnx`` lo ignora."""

    dimension: int = 384
    """Dimensión del vector de embedding. Debe coincidir con la del modelo.

    ``multilingual-e5-small`` produce 384 y no admite otro valor. Con el provider
    ``openai`` viaja como parámetro ``dimensions`` del request, que sí recorta el
    vector. Forma parte de la clave del cache
    ``(content_hash, provider, dimension)``, así que cambiarla invalida las
    entradas viejas sin colisionar — pero NO migra las DB de vectores ya escritas."""

    cache_filename: RuntimePath = "data/embedding_cache.db"
    """Fichero SQLite del cache de embeddings, para no re-vectorizar texto repetido.

    Relativo al home de instancia; se reancla con ``--home`` / ``INAKI_HOME``. Es
    un cache puro: borrarlo solo cuesta recalcular."""
