"""PillowPhotoAnnotator — dibuja rectángulos etiquetados sobre caras no-identificadas.

Solo anota caras con estado ``UNKNOWN`` o ``AMBIGUOUS``. Las caras ``MATCHED``
se ignoran silenciosamente (ya se sabe quiénes son, no hace falta etiquetarlas).

La etiqueta de cada cara es el idx extraído del face_ref ("{history_id}#{idx}"),
de forma que "[0]" en la imagen corresponde directamente a "face_ref: X#0" en el
contexto textual que recibe el agente. Esto elimina ambigüedad al llamar register_face.

Usa Pillow (PIL) con CPU puro. No requiere GPU ni dependencias pesadas.
Fuente: fallback al bitmap embebido de Pillow si no hay fuente del sistema.
Determinístico: mismo input → mismo output.
"""

from __future__ import annotations

import io
import logging

from PIL import Image, ImageDraw, ImageFont

from core.domain.entities.face import FaceMatch, MatchStatus

logger = logging.getLogger(__name__)

# Color del rectángulo y del texto numerado
_COLOR_RECT = (255, 0, 0)  # rojo
_COLOR_TEXTO = (255, 255, 255)  # blanco
_COLOR_FONDO_TEXTO = (255, 0, 0)  # fondo rojo para el número
_GROSOR_BORDE = 2
_PADDING_TEXTO = 2


def _cargar_fuente(tamaño: int = 16) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Carga una fuente con fallback al bitmap embebido.

    Intenta cargar ``DejaVuSans`` o ``Arial``. Si no encuentra ninguna,
    usa la fuente bitmap por defecto de Pillow — siempre disponible.
    """
    for nombre in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "Arial.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(nombre, tamaño)
        except (IOError, OSError):
            continue
    # Fallback garantizado
    return ImageFont.load_default()


class PillowPhotoAnnotator:
    """Anota una imagen JPEG/PNG dibujando rectángulos numerados sobre caras no-matched.

    Diseñado para ser determinístico e independiente del estado global.
    Se instancia sin parámetros; no tiene estado mutable.
    """

    def draw_numbered(self, image_bytes: bytes, caras: list[FaceMatch]) -> bytes:
        """Dibuja rectángulos numerados sobre las caras UNKNOWN y AMBIGUOUS.

        Args:
            image_bytes: Bytes de la imagen original (JPEG o PNG).
            caras: Lista de ``FaceMatch`` con bbox y status. Solo se anotan
                   las caras con status ``UNKNOWN`` o ``AMBIGUOUS``.

        Returns:
            Bytes de la imagen resultante en formato JPEG.
            Si no hay caras a anotar, devuelve la imagen re-codificada como JPEG
            (puede diferir en tamaño por la recompresión, pero es equivalente).
        """
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        draw = ImageDraw.Draw(img)
        fuente = _cargar_fuente(16)

        for cara in caras:
            if cara.status not in (MatchStatus.UNKNOWN, MatchStatus.AMBIGUOUS):
                continue

            bbox = cara.bbox
            x0 = bbox.x
            y0 = bbox.y
            x1 = bbox.x + bbox.w
            y1 = bbox.y + bbox.h

            # Rectángulo rojo alrededor de la cara
            draw.rectangle(
                [x0, y0, x1, y1],
                outline=_COLOR_RECT,
                width=_GROSOR_BORDE,
            )

            # Etiqueta: idx extraído del face_ref ("{history_id}#{idx}") para que
            # coincida exactamente con lo que el agente ve en el contexto textual.
            idx_str = cara.face_ref.split("#")[-1] if "#" in cara.face_ref else cara.face_ref
            etiqueta = f"[{idx_str}]"
            try:
                # Pillow ≥ 9.2: getbbox disponible
                caja_texto = fuente.getbbox(etiqueta)
                ancho_texto = caja_texto[2] - caja_texto[0]
                alto_texto = caja_texto[3] - caja_texto[1]
            except AttributeError:
                # Fallback para fuentes bitmap antiguas
                ancho_texto, alto_texto = draw.textlength(etiqueta, font=fuente), 16

            tx0 = x0
            ty0 = max(0, y0 - alto_texto - _PADDING_TEXTO * 2)
            tx1 = x0 + ancho_texto + _PADDING_TEXTO * 2
            ty1 = y0

            # Fondo opaco para el número
            draw.rectangle([tx0, ty0, tx1, ty1], fill=_COLOR_FONDO_TEXTO)
            draw.text(
                (tx0 + _PADDING_TEXTO, ty0 + _PADDING_TEXTO),
                etiqueta,
                fill=_COLOR_TEXTO,
                font=fuente,
            )

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92)
        return buf.getvalue()
