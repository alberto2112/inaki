"""Lógica compartida para adaptadores compatibles con OpenAI Chat Completions.

OpenAI y Groq usan el mismo formato de payload (OpenAI-compatible).
Esta función privada evita duplicación entre los dos adaptadores.
NO exportar: es un detalle de implementación del paquete scene.
"""

from __future__ import annotations

import base64
import logging

import httpx

from core.domain.errors import SceneDescriptionError

logger = logging.getLogger(__name__)

_PROMPT_DEFAULT = (
    "Describí la escena de esta imagen en español. "
    "Incluí el entorno, las personas presentes, los objetos relevantes y las actividades visibles. "
    "Sé preciso y objetivo."
)

_MAX_TOKENS = 1024


def resolver_prompt(
    prompt: str | None,
    prompt_template: str | None,
) -> str:
    """Resuelve el prompt con prioridad: argumento > template > default."""
    if prompt is not None:
        return prompt
    if prompt_template is not None:
        return prompt_template
    return _PROMPT_DEFAULT


async def describir_imagen_openai_compat(
    image_bytes: bytes,
    prompt: str,
    modelo: str,
    base_url: str,
    headers: dict[str, str],
    nombre_proveedor: str,
) -> str:
    """Llama a un endpoint compatible con OpenAI Chat Completions (visión multimodal).

    Args:
        image_bytes: Bytes de la imagen JPEG.
        prompt: Texto del prompt (ya resuelto por el adaptador).
        modelo: Nombre del modelo a usar.
        base_url: URL del endpoint (chat/completions).
        headers: Headers HTTP ya construidos por el adaptador.
        nombre_proveedor: Nombre del proveedor para mensajes de error.

    Returns:
        Texto de descripción de la escena.

    Raises:
        SceneDescriptionError: Si el proveedor falla.
    """
    datos_b64 = base64.b64encode(image_bytes).decode()

    payload = {
        "model": modelo,
        "messages": [
            {
                "role": "system",
                "content": prompt,
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{datos_b64}",
                        },
                    },
                ],
            },
        ],
        "max_completion_tokens": _MAX_TOKENS,
    }

    try:
        async with httpx.AsyncClient(timeout=60) as cliente:
            respuesta = await cliente.post(
                base_url,
                headers=headers,
                json=payload,
            )
            respuesta.raise_for_status()
            datos = respuesta.json()
    except httpx.HTTPStatusError as exc:
        cuerpo = exc.response.text[:500]
        raise SceneDescriptionError(
            f"{nombre_proveedor} HTTP {exc.response.status_code}: {cuerpo}"
        ) from exc
    except httpx.HTTPError as exc:
        raise SceneDescriptionError(
            f"Error de conexión con {nombre_proveedor}: {exc}"
        ) from exc

    try:
        texto = datos["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SceneDescriptionError(
            f"Respuesta de {nombre_proveedor} inesperada: {datos}"
        ) from exc

    logger.info("%s escena descrita: %d chars", nombre_proveedor, len(texto))
    return texto
