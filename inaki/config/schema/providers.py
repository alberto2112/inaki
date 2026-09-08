"""Bloque ``providers``: credenciales y endpoints de cada vendor.

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

from pydantic import ConfigDict, Field
from inaki.config.schema._base import _ConfigBaseModel


class ProviderConfig(_ConfigBaseModel):
    """
    Entrada del registry top-level de proveedores.

    Cada entrada representa UN vendor (groq, openai, openrouter, ollama, etc.)
    con sus credenciales y endpoint. Las features (`llm`, `embedding`,
    `transcription`, `memories.llm`) referencian entradas por nombre vía su
    campo ``provider: <key>``, eliminando duplicación de ``api_key``/``base_url``.

    ``extra="forbid"`` atrapa typos temprano (``api_ky``).
    """

    model_config = ConfigDict(extra="forbid")

    type: str | None = None
    """Nombre del adapter que implementa este vendor. ``null`` → se usa la key del dict.

    Con ``providers.groq: {...}`` el tipo se resuelve solo a ``"groq"``. Solo se
    explicita para tener DOS entradas del mismo adapter con credenciales
    distintas: ``providers.groq-work: {type: groq, api_key: K2}`` deja que
    ``llm.provider: groq-work`` apunte al adapter groq con otra cuenta."""

    api_key: str | None = Field(default=None, json_schema_extra={"secret": True})
    """Credencial del vendor. Es un SECRETO: ``inaki config show`` lo redacta.

    Opcional para los providers locales que no la piden (``ollama``, ``e5_onnx``);
    los adapters que sí la requieren fallan al construirse si falta."""

    base_url: str | None = None
    """Endpoint del vendor. ``null`` → el default hardcodeado del adapter.

    Obligatorio para servidores de inferencia propios OpenAI-compat (vLLM,
    llama.cpp), que no tienen default posible."""
