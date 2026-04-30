"""Adaptador de descripción de escena via Groq Vision API (compatible con OpenAI).

Implementa ``ISceneDescriberPort`` usando el endpoint de visión de Groq,
que es compatible con el formato OpenAI Chat Completions.
Se auto-descubre via ``PROVIDER_NAME`` (mismo patrón que los LLM providers).

Endpoint: POST https://api.groq.com/openai/v1/chat/completions
Headers requeridos: Authorization Bearer, Content-Type.
Payload: OpenAI-compatible (mismo formato que OpenAISceneDescriberAdapter).
Response: choices[0].message.content

Modelo por defecto recomendado: llama-3.2-90b-vision-preview
"""

from __future__ import annotations

from adapters.outbound.scene._openai_compat import (
    describir_imagen_openai_compat,
    resolver_prompt,
)
from core.ports.outbound.scene_describer_port import ISceneDescriberPort

PROVIDER_NAME = "groq"

_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


class GroqSceneDescriberAdapter(ISceneDescriberPort):
    """Describe imágenes usando Groq Llama Vision (API compatible con OpenAI)."""

    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.2-90b-vision-preview",
        prompt_template: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._modelo = model
        self._prompt_template = prompt_template
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def describe_image(
        self,
        image_bytes: bytes,
        prompt: str | None = None,
    ) -> str:
        """Describe la escena de una imagen usando Groq Vision multimodal.

        Args:
            image_bytes: Bytes de la imagen (JPEG o PNG).
            prompt: Prompt personalizado. Si es None, usa prompt_template o el default.

        Returns:
            Descripción de la escena en español.

        Raises:
            SceneDescriptionError: Si Groq falla, timeout o error HTTP.
        """
        texto_prompt = resolver_prompt(prompt, self._prompt_template)

        return await describir_imagen_openai_compat(
            image_bytes=image_bytes,
            prompt=texto_prompt,
            modelo=self._modelo,
            base_url=_ENDPOINT,
            headers=self._headers,
            nombre_proveedor="Groq",
        )
