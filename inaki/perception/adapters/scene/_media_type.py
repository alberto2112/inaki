"""Detección del media type de una imagen a partir de sus bytes (magic bytes).

Los vendors multimodales validan que el ``media_type`` declarado coincida con los
bytes: Anthropic rechaza con 400 un PNG declarado como ``image/jpeg``. El mime que
reporta el canal no es fiable (Telegram lo fija, un documento lo declara el
cliente), así que la verdad sale de la cabecera del fichero.
NO exportar: es un detalle de implementación del paquete scene.
"""

from __future__ import annotations

from inaki.shared.errors import SceneDescriptionError


def detectar_media_type(image_bytes: bytes) -> str:
    """Devuelve el media type de la imagen según su cabecera.

    Cubre los cuatro formatos que aceptan Anthropic, OpenAI, Groq y DeepSeek: JPEG, PNG,
    GIF y WebP.

    Raises:
        SceneDescriptionError: Si la cabecera no es de ningún formato soportado.
    """
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    raise SceneDescriptionError(
        f"Formato de imagen no soportado para descripción de escena "
        f"(cabecera: {image_bytes[:12]!r}). Soportados: JPEG, PNG, GIF, WebP."
    )
