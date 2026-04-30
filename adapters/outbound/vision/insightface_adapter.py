"""InsightFaceVisionAdapter — detección facial y embedding con InsightFace.

InsightFace es una dependencia pesada (~400MB de modelo). Para no retrasar
el arranque del daemon, el modelo se inicializa en la primera llamada a
``detect_and_embed()`` (lazy-load). El constructor NO carga nada.

Patrón de lazy-load:
    _app = None  →  primera llamada  →  _get_app()  →  FaceAnalysis + prepare()

Thread-safety: no se usa threading; el daemon corre en un event loop asyncio
y las llamadas son secuenciales. Si en el futuro se paraleliza, añadir un Lock.

PROVIDER_NAME se define a nivel módulo para que el container pueda auto-descubrir
este adaptador por scanning (mismo patrón que LLM providers).
"""

from __future__ import annotations

import io
import logging
from typing import Any

import numpy as np
from PIL import Image

from core.domain.entities.face import BBox, FaceDetection
from core.domain.errors import VisionError
from core.ports.outbound.vision_port import IVisionPort

logger = logging.getLogger(__name__)

PROVIDER_NAME = "insightface"

# Proveedor de contexto de ejecución para InsightFace
_CTX_ID = "cpu"


class InsightFaceVisionAdapter(IVisionPort):
    """Adaptador de visión basado en InsightFace (buffalo_sc por defecto).

    Args:
        nombre_modelo: Nombre del modelo InsightFace a cargar.
                       Por defecto ``"buffalo_sc"`` (512d, CPU-friendly).
    """

    def __init__(self, nombre_modelo: str = "buffalo_sc") -> None:
        self._nombre_modelo = nombre_modelo
        self._app: Any | None = None  # lazy — NO inicializar aquí

    def _get_app(self) -> Any:
        """Lazy singleton del modelo InsightFace.

        Se llama solo en la primera ejecución de ``detect_and_embed``.
        El import de insightface también ocurre aquí para que el módulo
        sea importable sin tener insightface instalado (útil en CI/test).
        """
        if self._app is None:
            logger.info(
                "Inicializando InsightFace modelo='%s' ctx='%s' (primera detección)...",
                self._nombre_modelo,
                _CTX_ID,
            )
            import insightface.app as _insightface_app

            app = _insightface_app.FaceAnalysis(
                name=self._nombre_modelo,
                providers=["CPUExecutionProvider"],
            )
            app.prepare(ctx_id=0, det_size=(640, 640))
            self._app = app
            logger.info("InsightFace listo.")
        return self._app

    async def detect_and_embed(self, image_bytes: bytes) -> list[FaceDetection]:
        """Detecta caras en la imagen y devuelve sus embeddings.

        Decodifica los bytes con PIL → convierte a RGB → pasa a InsightFace.
        InsightFace devuelve una lista de objetos con atributos:
          - ``bbox``: np.ndarray([x1, y1, x2, y2])
          - ``embedding``: np.ndarray de 512 floats
          - ``det_score``: float

        Args:
            image_bytes: Bytes JPEG o PNG de la imagen.

        Returns:
            Lista de FaceDetection, ordenada por detection_score desc.
            Lista vacía si no se detectan caras.

        Raises:
            VisionError: Si la imagen es inválida o el modelo falla.
        """
        try:
            img_pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            img_np = np.array(img_pil)

            app = self._get_app()
            caras_raw = app.get(img_np)
        except VisionError:
            raise
        except Exception as exc:
            raise VisionError(
                f"Error en detección facial (modelo '{self._nombre_modelo}'): {exc}"
            ) from exc

        resultados: list[FaceDetection] = []
        for cara in caras_raw:
            bbox_arr = cara.bbox  # [x1, y1, x2, y2] float
            x1, y1, x2, y2 = (int(round(v)) for v in bbox_arr)
            embedding_lista: list[float] = cara.embedding.tolist()
            score: float = float(cara.det_score)

            resultados.append(
                FaceDetection(
                    bbox=BBox(x=x1, y=y1, w=x2 - x1, h=y2 - y1),
                    embedding=embedding_lista,
                    detection_score=score,
                )
            )

        # Ordenar por score desc (InsightFace generalmente ya lo hace, pero garantizamos)
        resultados.sort(key=lambda fd: fd.detection_score, reverse=True)
        return resultados
