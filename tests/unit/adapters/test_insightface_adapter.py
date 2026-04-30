"""Tests para InsightFaceVisionAdapter.

Cubre:
- IF-01: lazy-load — FaceAnalysis NO se llama en __init__, solo en detect_and_embed
- IF-02: detect_and_embed devuelve lista de FaceDetection con bbox, embedding list[float], detection_score
- IF-03: resultado vacío cuando FaceAnalysis devuelve []
- IF-04: error durante detección → VisionError
- IF-05: model_name se pasa a FaceAnalysis(name=model_name)

El módulo 'insightface' se mockea vía sys.modules antes del import para no
requerir la librería instalada en el entorno de test.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch, call
import importlib

import numpy as np
import pytest

from core.domain.entities.face import BBox, FaceDetection
from core.domain.errors import VisionError


# ---------------------------------------------------------------------------
# Mock de insightface en sys.modules ANTES de importar el adaptador
# ---------------------------------------------------------------------------

def _make_insightface_mock() -> tuple[MagicMock, MagicMock]:
    """Crea mocks para el módulo insightface y FaceAnalysis.

    Retorna (modulo_mock, FaceAnalysis_class_mock).
    FaceAnalysis_class_mock es la clase mockeada que se usará como spy.
    """
    face_analysis_class_mock = MagicMock(name="FaceAnalysis")
    insightface_mock = MagicMock(name="insightface")
    insightface_app_mock = MagicMock(name="insightface.app")
    insightface_app_mock.FaceAnalysis = face_analysis_class_mock
    insightface_mock.app = insightface_app_mock
    return insightface_mock, face_analysis_class_mock


def _fake_face(bbox_array, embedding_array, det_score: float = 0.95):
    """Crea un objeto face falso tal como lo devuelve InsightFace."""
    face = MagicMock()
    face.bbox = bbox_array
    face.embedding = embedding_array
    face.det_score = det_score
    return face


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_insightface_module():
    """Inyecta el mock de insightface en sys.modules para todos los tests del módulo."""
    insightface_mock, _ = _make_insightface_mock()
    # Registrar submódulos también
    sys.modules.setdefault("insightface", insightface_mock)
    sys.modules.setdefault("insightface.app", insightface_mock.app)
    yield insightface_mock
    # Limpiar para no afectar otros tests
    sys.modules.pop("insightface", None)
    sys.modules.pop("insightface.app", None)
    # Limpiar el módulo del adaptador para que se reimporte fresco en cada test
    sys.modules.pop("adapters.outbound.vision.insightface_adapter", None)
    sys.modules.pop("adapters.outbound.vision", None)


@pytest.fixture
def face_analysis_class(mock_insightface_module):
    """Devuelve el FaceAnalysis class mock inyectado."""
    return mock_insightface_module.app.FaceAnalysis


@pytest.fixture
def adaptador(mock_insightface_module):
    """Instancia el InsightFaceVisionAdapter con el módulo mockeado."""
    from adapters.outbound.vision.insightface_adapter import InsightFaceVisionAdapter
    return InsightFaceVisionAdapter(nombre_modelo="buffalo_sc")


# ---------------------------------------------------------------------------
# Helpers de imagen
# ---------------------------------------------------------------------------

def _make_jpeg_bytes(ancho: int = 100, alto: int = 100) -> bytes:
    """Crea un JPEG mínimo usando PIL."""
    from PIL import Image
    import io
    img = Image.new("RGB", (ancho, alto), color="gray")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_if01_lazy_load_no_llama_face_analysis_en_constructor(
    face_analysis_class, mock_insightface_module
):
    """IF-01: FaceAnalysis NO debe llamarse al construir el adaptador."""
    from adapters.outbound.vision.insightface_adapter import InsightFaceVisionAdapter

    face_analysis_class.reset_mock()
    _ = InsightFaceVisionAdapter(nombre_modelo="buffalo_sc")

    face_analysis_class.assert_not_called()


async def test_if01_face_analysis_se_llama_en_detect_and_embed(
    adaptador, face_analysis_class
):
    """IF-01 (parte 2): FaceAnalysis se instancia en la primera llamada a detect_and_embed."""
    # Configurar el mock para que prepare() no falle
    instancia_app = MagicMock()
    instancia_app.get.return_value = []
    face_analysis_class.return_value = instancia_app

    imagen = _make_jpeg_bytes()
    await adaptador.detect_and_embed(imagen)

    # Debe haberse llamado al menos una vez (lazy init)
    assert face_analysis_class.call_count >= 1


async def test_if02_detect_and_embed_devuelve_lista_face_detection(
    adaptador, face_analysis_class
):
    """IF-02: detect_and_embed retorna lista de FaceDetection válidos."""
    bbox_np = np.array([10.0, 20.0, 110.0, 170.0], dtype=np.float32)
    embedding_np = np.random.default_rng(42).random(512).astype(np.float32)
    cara_fake = _fake_face(bbox_np, embedding_np, det_score=0.97)

    instancia_app = MagicMock()
    instancia_app.get.return_value = [cara_fake]
    face_analysis_class.return_value = instancia_app

    imagen = _make_jpeg_bytes()
    resultado = await adaptador.detect_and_embed(imagen)

    assert len(resultado) == 1
    fd = resultado[0]
    assert isinstance(fd, FaceDetection)
    assert isinstance(fd.bbox, BBox)
    assert fd.bbox.x == 10
    assert fd.bbox.y == 20
    assert fd.bbox.w == 100  # 110 - 10
    assert fd.bbox.h == 150  # 170 - 20
    assert len(fd.embedding) == 512
    assert isinstance(fd.embedding[0], float)
    assert abs(fd.detection_score - 0.97) < 1e-4


async def test_if03_resultado_vacio_cuando_no_hay_caras(adaptador, face_analysis_class):
    """IF-03: FaceAnalysis devuelve [] → detect_and_embed retorna []."""
    instancia_app = MagicMock()
    instancia_app.get.return_value = []
    face_analysis_class.return_value = instancia_app

    imagen = _make_jpeg_bytes()
    resultado = await adaptador.detect_and_embed(imagen)

    assert resultado == []


async def test_if04_error_en_deteccion_lanza_vision_error(adaptador, face_analysis_class):
    """IF-04: excepción del modelo → se relanza como VisionError."""
    instancia_app = MagicMock()
    instancia_app.get.side_effect = RuntimeError("modelo explotó")
    face_analysis_class.return_value = instancia_app

    imagen = _make_jpeg_bytes()
    with pytest.raises(VisionError):
        await adaptador.detect_and_embed(imagen)


async def test_if05_model_name_se_pasa_a_face_analysis(face_analysis_class):
    """IF-05: el model_name pasado al constructor llega como name= a FaceAnalysis."""
    from adapters.outbound.vision.insightface_adapter import InsightFaceVisionAdapter

    instancia_app = MagicMock()
    instancia_app.get.return_value = []
    face_analysis_class.return_value = instancia_app

    adaptador = InsightFaceVisionAdapter(nombre_modelo="mi_modelo_custom")
    imagen = _make_jpeg_bytes()
    await adaptador.detect_and_embed(imagen)

    # Verificar que FaceAnalysis fue llamado con name="mi_modelo_custom"
    llamadas = face_analysis_class.call_args_list
    assert len(llamadas) >= 1
    _, kwargs = llamadas[0]
    assert kwargs.get("name") == "mi_modelo_custom" or llamadas[0].args[0] == "mi_modelo_custom"


def test_if_provider_name_constant():
    """PROVIDER_NAME debe estar definido a nivel módulo."""
    from adapters.outbound.vision import insightface_adapter
    assert hasattr(insightface_adapter, "PROVIDER_NAME")
    assert insightface_adapter.PROVIDER_NAME == "insightface"


async def test_if02_multiples_caras(adaptador, face_analysis_class):
    """IF-02 (multiples): 3 caras → lista con 3 FaceDetection."""
    caras_fake = []
    for i in range(3):
        bbox_np = np.array([i * 50.0, 10.0, i * 50.0 + 40.0, 90.0], dtype=np.float32)
        emb_np = np.random.default_rng(i).random(512).astype(np.float32)
        caras_fake.append(_fake_face(bbox_np, emb_np, det_score=0.9 - i * 0.1))

    instancia_app = MagicMock()
    instancia_app.get.return_value = caras_fake
    face_analysis_class.return_value = instancia_app

    imagen = _make_jpeg_bytes(ancho=200, alto=100)
    resultado = await adaptador.detect_and_embed(imagen)

    assert len(resultado) == 3
    for fd in resultado:
        assert isinstance(fd, FaceDetection)
        assert len(fd.embedding) == 512
