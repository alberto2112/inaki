"""Port para el proveedor de descripción de escena (LLM multimodal).

El adaptador concreto (AnthropicSceneDescriberAdapter, OpenAISceneDescriberAdapter,
GroqSceneDescriberAdapter) implementa este port. El proveedor se auto-descubre
via ``PROVIDER_NAME`` module-level constant (mismo patrón que LLM providers).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ISceneDescriberPort(ABC):
    @abstractmethod
    async def describe_image(
        self,
        image_bytes: bytes,
        prompt: str | None = None,
    ) -> str:
        """Describe la escena de una imagen usando un LLM multimodal.

        Se llama SIEMPRE durante el procesamiento de una foto, incluso cuando
        no hay caras detectadas. El prompt built-in está en español.

        Args:
            image_bytes: Bytes de la imagen (JPEG o PNG).
            prompt: Prompt personalizado. Si es None, usa el prompt built-in
                    del adaptador (en español).

        Returns:
            Descripción de la escena en español.

        Raises:
            SceneDescriptionError: Si el proveedor falla, timeout o imagen inválida.
        """
        ...
