"""Adaptador de descripción de escena via OpenAI Chat Completions API (visión).

Implementa ``ISceneDescriberPort`` usando el endpoint multimodal de OpenAI.
Se auto-descubre via ``PROVIDER_NAME`` (mismo patrón que los LLM providers).

Endpoint: POST https://api.openai.com/v1/chat/completions
Headers requeridos: Authorization Bearer, Content-Type.
Payload: mensaje de usuario con bloque text (prompt) + image_url (base64 data URL).
Response: choices[0].message.content
"""

from __future__ import annotations

from adapters.outbound.scene._openai_compat import (
    describir_imagen_openai_compat,
    resolver_prompt,
)
from core.ports.outbound.scene_describer_port import ISceneDescriberPort

PROVIDER_NAME = "openai"

_ENDPOINT = "https://api.openai.com/v1/chat/completions"


class OpenAISceneDescriberAdapter(ISceneDescriberPort):
    """Describe imágenes usando OpenAI GPT-4o (o similar) con visión multimodal."""

    def __init__(
        self,
        api_key: str,
        model: str,
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
        """Describe la escena de una imagen usando OpenAI multimodal.

        Args:
            image_bytes: Bytes de la imagen (JPEG o PNG).
            prompt: Prompt personalizado. Si es None, usa prompt_template o el default.

        Returns:
            Descripción de la escena en español.

        Raises:
            SceneDescriptionError: Si OpenAI falla, timeout o error HTTP.
        """
        texto_prompt = resolver_prompt(prompt, self._prompt_template)

        return await describir_imagen_openai_compat(
            image_bytes=image_bytes,
            prompt=texto_prompt,
            modelo=self._modelo,
            base_url=_ENDPOINT,
            headers=self._headers,
            nombre_proveedor="OpenAI",
        )
