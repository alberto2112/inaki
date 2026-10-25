"""Adaptador de descripción de escena via DeepSeek Vision (compatible con OpenAI).

Implementa ``ISceneDescriberPort`` usando el endpoint Chat Completions de DeepSeek,
que acepta bloques ``image_url`` con data URL base64 en los mensajes ``user``.
Se auto-descubre via ``PROVIDER_NAME`` (mismo patrón que los LLM providers).

Endpoint: POST https://api.deepseek.com/chat/completions
Headers requeridos: Authorization Bearer, Content-Type.
Payload: OpenAI-compatible, con dos diferencias propias de DeepSeek:
  - el tope de salida va en ``max_tokens`` (no ``max_completion_tokens``);
  - ``thinking`` desactivado explícito: describir una foto no necesita razonamiento,
    y con thinking el razonamiento se come el tope de tokens y la respuesta puede
    llegar vacía.
Response: choices[0].message.content

Modelo por defecto: ``deepseek-flash`` (el único con visión; ver
https://api-docs.deepseek.com/guides/vision). La imagen solo puede ir en un mensaje
``user`` — en ``system`` o ``assistant`` la API responde 400.
"""

from __future__ import annotations

from inaki.perception.adapters.scene._openai_compat import (
    MAX_TOKENS,
    describir_imagen_openai_compat,
    resolver_prompt,
)
from inaki.perception.ports.scene import ISceneDescriberPort

PROVIDER_NAME = "deepseek"

_ENDPOINT = "https://api.deepseek.com/chat/completions"


class DeepSeekSceneDescriberAdapter(ISceneDescriberPort):
    """Describe imágenes usando DeepSeek Flash con visión (API compatible con OpenAI)."""

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-flash",
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
        """Describe the scene of an image using DeepSeek Vision multimodal.

        Args:
            image_bytes: Bytes de la imagen (JPEG, PNG, GIF o WebP).
            prompt: Prompt personalizado. Si es None, usa prompt_template o el default.

        Returns:
            Description of the scene.

        Raises:
            SceneDescriptionError: Si DeepSeek falla, timeout o error HTTP.
        """
        texto_prompt = resolver_prompt(prompt, self._prompt_template)

        return await describir_imagen_openai_compat(
            image_bytes=image_bytes,
            prompt=texto_prompt,
            modelo=self._modelo,
            base_url=_ENDPOINT,
            headers=self._headers,
            nombre_proveedor="DeepSeek",
            parametros_salida={"max_tokens": MAX_TOKENS, "thinking": {"type": "disabled"}},
        )
