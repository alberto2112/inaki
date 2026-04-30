"""Adaptador de descripción de escena via Anthropic Messages API.

Implementa ``ISceneDescriberPort`` usando el endpoint multimodal de Anthropic.
Se auto-descubre via ``PROVIDER_NAME`` (mismo patrón que los LLM providers).

Endpoint: POST https://api.anthropic.com/v1/messages
Headers requeridos: x-api-key, anthropic-version, content-type.
Payload: mensaje de usuario con bloque imagen (base64) + bloque texto (prompt).
Response: content[0].text
"""

from __future__ import annotations

import base64
import logging

import httpx

from core.domain.errors import SceneDescriptionError
from core.ports.outbound.scene_describer_port import ISceneDescriberPort

PROVIDER_NAME = "anthropic"

logger = logging.getLogger(__name__)

_PROMPT_DEFAULT = (
    "Describí la escena de esta imagen en español. "
    "Incluí el entorno, las personas presentes, los objetos relevantes y las actividades visibles. "
    "Sé preciso y objetivo."
)

_ANTHROPIC_VERSION = "2023-06-01"
_ENDPOINT = "https://api.anthropic.com/v1/messages"
_MAX_TOKENS = 1024


class AnthropicSceneDescriberAdapter(ISceneDescriberPort):
    """Describe imágenes usando Anthropic Claude con visión multimodal."""

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
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def _resolver_prompt(self, prompt: str | None) -> str:
        if prompt is not None:
            return prompt
        if self._prompt_template is not None:
            return self._prompt_template
        return _PROMPT_DEFAULT

    async def describe_image(
        self,
        image_bytes: bytes,
        prompt: str | None = None,
    ) -> str:
        """Describe la escena de una imagen usando Claude multimodal.

        Args:
            image_bytes: Bytes de la imagen (JPEG o PNG).
            prompt: Prompt personalizado. Si es None, usa prompt_template o el default.

        Returns:
            Descripción de la escena en español.

        Raises:
            SceneDescriptionError: Si Anthropic falla, timeout o error HTTP.
        """
        texto_prompt = self._resolver_prompt(prompt)
        datos_b64 = base64.b64encode(image_bytes).decode()

        payload = {
            "model": self._modelo,
            "max_tokens": _MAX_TOKENS,
            "system": texto_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": datos_b64,
                            },
                        },
                    ],
                }
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=60) as cliente:
                respuesta = await cliente.post(
                    _ENDPOINT,
                    headers=self._headers,
                    json=payload,
                )
                respuesta.raise_for_status()
                datos = respuesta.json()
        except httpx.HTTPStatusError as exc:
            cuerpo = exc.response.text[:500]
            raise SceneDescriptionError(
                f"Anthropic HTTP {exc.response.status_code}: {cuerpo}"
            ) from exc
        except httpx.HTTPError as exc:
            raise SceneDescriptionError(
                f"Error de conexión con Anthropic: {exc}"
            ) from exc

        try:
            texto = datos["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SceneDescriptionError(
                f"Respuesta de Anthropic inesperada: {datos}"
            ) from exc

        logger.info("Anthropic escena descrita: %d chars", len(texto))
        return texto
