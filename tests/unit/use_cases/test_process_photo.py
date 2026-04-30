"""Tests del caso de uso ProcessPhotoUseCase.

Cubre los 6 escenarios críticos:
1. disabled — photos.enabled=False bypass
2. no_faces — solo descripción de escena
3. single_match_private — cara conocida en chat privado
4. single_unknown_private — cara desconocida en chat privado (anotada)
5. unknown_in_group — cara desconocida en grupo (sin anotación)
6. ignored_filtered — cara ignorada filtrada silenciosamente
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.domain.entities.face import (
    BBox,
    FaceDetection,
    FaceMatch,
    MatchStatus,
    Person,
)
from core.use_cases.process_photo import ProcessPhotoUseCase
from infrastructure.config import FacesConfig, PhotosConfig


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _detection(score: float = 0.95) -> FaceDetection:
    return FaceDetection(
        bbox=BBox(x=10, y=20, w=100, h=150),
        embedding=[0.1] * 512,
        detection_score=score,
    )


def _candidate_match(persona: Person, score: float) -> FaceMatch:
    """Simula el output del registry: un FaceMatch placeholder por candidato."""
    return FaceMatch(
        face_ref="",
        bbox=BBox(x=0, y=0, w=0, h=0),
        candidates=[(persona, score)],
        status=MatchStatus.UNKNOWN,
        categoria=persona.categoria,
    )


def _build_use_case(
    *,
    photos_config: PhotosConfig | None = None,
    scene_describer: AsyncMock | None = None,
) -> tuple[ProcessPhotoUseCase, dict]:
    """Construye el use case con todos los mocks. Devuelve (uc, mocks_dict)."""
    vision = AsyncMock()
    vision.detect_and_embed.return_value = []

    face_registry = AsyncMock()
    face_registry.find_matches.return_value = []

    annotator = MagicMock()
    annotator.draw_numbered.return_value = b"\xff\xd8\xff_anotada"

    metadata_repo = AsyncMock()
    metadata_repo.save.return_value = None

    if scene_describer is None:
        scene_describer = AsyncMock()
        scene_describer.describe_image.return_value = "Una persona en un café."

    config = photos_config or PhotosConfig()

    uc = ProcessPhotoUseCase(
        vision=vision,
        face_registry=face_registry,
        scene_describer=scene_describer,
        annotator=annotator,
        metadata_repo=metadata_repo,
        config=config,
    )
    mocks = {
        "vision": vision,
        "face_registry": face_registry,
        "annotator": annotator,
        "metadata_repo": metadata_repo,
        "scene_describer": scene_describer,
    }
    return uc, mocks


# ----------------------------------------------------------------------
# 1. disabled
# ----------------------------------------------------------------------


async def test_disabled_devuelve_skip_y_no_llama_a_nada():
    config = PhotosConfig(enabled=False)
    uc, mocks = _build_use_case(photos_config=config)

    resultado = await uc.execute(
        image_bytes=b"\xff\xd8\xff",
        history_id=1,
        agent_id="test",
        channel="telegram",
        chat_id="chat-1",
        chat_type="private",
    )

    assert resultado.should_skip_run_agent is True
    assert resultado.text_context == ""
    assert resultado.annotated_image is None
    mocks["vision"].detect_and_embed.assert_not_called()
    mocks["face_registry"].find_matches.assert_not_called()
    mocks["scene_describer"].describe_image.assert_not_called()
    mocks["annotator"].draw_numbered.assert_not_called()
    mocks["metadata_repo"].save.assert_not_called()


# ----------------------------------------------------------------------
# 2. no_faces
# ----------------------------------------------------------------------


async def test_no_faces_devuelve_solo_descripcion_de_escena():
    uc, mocks = _build_use_case()
    mocks["vision"].detect_and_embed.return_value = []
    mocks["scene_describer"].describe_image.return_value = (
        "Un paisaje al atardecer."
    )

    resultado = await uc.execute(
        image_bytes=b"\xff\xd8\xff",
        history_id=10,
        agent_id="test",
        channel="telegram",
        chat_id="chat-1",
        chat_type="private",
    )

    assert resultado.should_skip_run_agent is False
    assert resultado.annotated_image is None
    assert "Un paisaje al atardecer." in resultado.text_context
    assert "Personas reconocidas" not in resultado.text_context
    assert "Caras desconocidas" not in resultado.text_context
    # No se persiste metadata porque no hay caras
    mocks["metadata_repo"].save.assert_not_called()


# ----------------------------------------------------------------------
# 3. single_match_private
# ----------------------------------------------------------------------


async def test_single_match_privado_menciona_persona_y_no_anota():
    uc, mocks = _build_use_case()
    alberto = Person(
        nombre="Alberto", apellido="Hernández", relacion="dueño", embeddings_count=3
    )
    mocks["vision"].detect_and_embed.return_value = [_detection()]
    mocks["face_registry"].find_matches.return_value = [
        _candidate_match(alberto, 0.91)
    ]

    resultado = await uc.execute(
        image_bytes=b"\xff\xd8\xff",
        history_id=42,
        agent_id="test",
        channel="telegram",
        chat_id="chat-1",
        chat_type="private",
    )

    assert resultado.should_skip_run_agent is False
    assert resultado.annotated_image is None
    assert "Alberto Hernández" in resultado.text_context
    assert "0.91" in resultado.text_context
    assert "Caras desconocidas" not in resultado.text_context
    mocks["annotator"].draw_numbered.assert_not_called()
    mocks["metadata_repo"].save.assert_called_once()


# ----------------------------------------------------------------------
# 4. single_unknown_private
# ----------------------------------------------------------------------


async def test_single_unknown_privado_devuelve_imagen_anotada():
    uc, mocks = _build_use_case()
    mocks["vision"].detect_and_embed.return_value = [_detection()]
    mocks["face_registry"].find_matches.return_value = []  # sin candidatos

    resultado = await uc.execute(
        image_bytes=b"\xff\xd8\xff_original",
        history_id=99,
        agent_id="test",
        channel="telegram",
        chat_id="chat-1",
        chat_type="private",
    )

    assert resultado.annotated_image == b"\xff\xd8\xff_anotada"
    assert "numeradas en la imagen anotada" in resultado.text_context
    # El face_ref concreto debe aparecer para que el agente no tenga que adivinarlo
    assert "face_ref: 99#0" in resultado.text_context
    assert "Cara [0]" in resultado.text_context
    # Cara UNKNOWN no tiene candidato → etiqueta "desconocida"
    assert "desconocida" in resultado.text_context
    mocks["annotator"].draw_numbered.assert_called_once()
    # Verificar que solo pasó las desconocidas al anotador
    args, _ = mocks["annotator"].draw_numbered.call_args
    assert args[0] == b"\xff\xd8\xff_original"
    desconocidas_pasadas = args[1]
    assert len(desconocidas_pasadas) == 1
    assert desconocidas_pasadas[0].status == MatchStatus.UNKNOWN
    mocks["metadata_repo"].save.assert_called_once()


# ----------------------------------------------------------------------
# 5. unknown_in_group
# ----------------------------------------------------------------------


async def test_unknown_en_grupo_no_anota_pero_describe_escena():
    uc, mocks = _build_use_case()
    mocks["vision"].detect_and_embed.return_value = [_detection()]
    mocks["face_registry"].find_matches.return_value = []

    resultado = await uc.execute(
        image_bytes=b"\xff\xd8\xff",
        history_id=100,
        agent_id="test",
        channel="telegram",
        chat_id="grupo-1",
        chat_type="group",
    )

    assert resultado.annotated_image is None
    assert "sin enrolamiento en grupos" in resultado.text_context
    mocks["annotator"].draw_numbered.assert_not_called()
    # Sigue persistiendo metadata para auditoría
    mocks["metadata_repo"].save.assert_called_once()


# ----------------------------------------------------------------------
# 6. ignored_filtered
# ----------------------------------------------------------------------


async def test_ignored_se_filtra_silenciosamente_pero_persiste_metadata():
    uc, mocks = _build_use_case()
    ignorada = Person(
        nombre=None, categoria="ignorada", embeddings_count=1
    )
    mocks["vision"].detect_and_embed.return_value = [_detection()]
    mocks["face_registry"].find_matches.return_value = [
        _candidate_match(ignorada, 0.92)  # Match alto
    ]

    resultado = await uc.execute(
        image_bytes=b"\xff\xd8\xff",
        history_id=200,
        agent_id="test",
        channel="telegram",
        chat_id="chat-1",
        chat_type="private",
    )

    # NO aparece en el texto
    assert "ignorada" not in resultado.text_context.lower()
    assert "Personas reconocidas" not in resultado.text_context
    assert "Caras desconocidas" not in resultado.text_context
    # No anota imagen (no hay desconocidas/ambiguas reales)
    assert resultado.annotated_image is None
    mocks["annotator"].draw_numbered.assert_not_called()
    # PERO persiste metadata para auditoría
    mocks["metadata_repo"].save.assert_called_once()
    # Verificamos que la metadata incluye la cara ignorada
    args, _ = mocks["metadata_repo"].save.call_args
    metadata = args[0]
    assert len(metadata.faces) == 1
    assert metadata.faces[0].categoria == "ignorada"


# ----------------------------------------------------------------------
# Bonus: thresholds - ambiguous case
# ----------------------------------------------------------------------


async def test_ambiguous_score_genera_anotacion_en_privado():
    """Score entre ambiguous_threshold (0.40) y match_threshold (0.55) → ambiguous."""
    config = PhotosConfig(faces=FacesConfig(match_threshold=0.55, ambiguous_threshold=0.40))
    uc, mocks = _build_use_case(photos_config=config)
    persona = Person(nombre="Quizás Alberto", embeddings_count=1)
    mocks["vision"].detect_and_embed.return_value = [_detection()]
    mocks["face_registry"].find_matches.return_value = [
        _candidate_match(persona, 0.45)  # ambiguo
    ]

    resultado = await uc.execute(
        image_bytes=b"\xff\xd8\xff",
        history_id=500,
        agent_id="test",
        channel="telegram",
        chat_id="chat-1",
        chat_type="private",
    )

    # Ambiguo se anota y el contexto expone el candidato con su score
    assert resultado.annotated_image is not None
    assert "numeradas en la imagen anotada" in resultado.text_context
    assert "Quizás Alberto" in resultado.text_context
    assert "0.45" in resultado.text_context
    assert "posible match" in resultado.text_context
    mocks["annotator"].draw_numbered.assert_called_once()


# ----------------------------------------------------------------------
# analysis_only: foto con caption suprime enrollment
# ----------------------------------------------------------------------


async def test_analysis_only_suprime_imagen_anotada():
    """Con analysis_only=True no se genera imagen anotada aunque haya caras desconocidas."""
    uc, mocks = _build_use_case()
    mocks["vision"].detect_and_embed.return_value = [_detection()]
    mocks["face_registry"].find_matches.return_value = []

    resultado = await uc.execute(
        image_bytes=b"\xff\xd8\xff",
        history_id=99,
        agent_id="test",
        channel="telegram",
        chat_id="chat-1",
        chat_type="private",
        analysis_only=True,
    )

    assert resultado.annotated_image is None
    mocks["annotator"].draw_numbered.assert_not_called()


async def test_analysis_only_suprime_face_refs_y_enrollment():
    """Con analysis_only=True el contexto no incluye face_ref ni sugerencias de registro."""
    uc, mocks = _build_use_case()
    mocks["vision"].detect_and_embed.return_value = [_detection()]
    mocks["face_registry"].find_matches.return_value = []

    resultado = await uc.execute(
        image_bytes=b"\xff\xd8\xff",
        history_id=99,
        agent_id="test",
        channel="telegram",
        chat_id="chat-1",
        chat_type="private",
        analysis_only=True,
    )

    assert "face_ref" not in resultado.text_context
    assert "add_photo_to_person" not in resultado.text_context
    assert "register_face" not in resultado.text_context


async def test_analysis_only_ambiguous_muestra_candidato_sin_enrollment():
    """Con analysis_only=True, una cara ambigua muestra el candidato pero sin face_ref."""
    config = PhotosConfig(faces=FacesConfig(match_threshold=0.55, ambiguous_threshold=0.40))
    uc, mocks = _build_use_case(photos_config=config)
    persona = Person(nombre="Alberto", embeddings_count=1)
    mocks["vision"].detect_and_embed.return_value = [_detection()]
    mocks["face_registry"].find_matches.return_value = [_candidate_match(persona, 0.47)]

    resultado = await uc.execute(
        image_bytes=b"\xff\xd8\xff",
        history_id=99,
        agent_id="test",
        channel="telegram",
        chat_id="chat-1",
        chat_type="private",
        analysis_only=True,
    )

    assert "Alberto" in resultado.text_context
    assert "0.47" in resultado.text_context
    assert "face_ref" not in resultado.text_context
    assert resultado.annotated_image is None


# ----------------------------------------------------------------------
# Bonus: scene_describer is None → graceful
# ----------------------------------------------------------------------


async def test_sin_scene_describer_no_hay_seccion_de_escena():
    uc, mocks = _build_use_case(scene_describer=None)
    # Pasamos un scene_describer None explícito
    uc._scene_describer = None
    mocks["vision"].detect_and_embed.return_value = []

    resultado = await uc.execute(
        image_bytes=b"\xff\xd8\xff",
        history_id=600,
        agent_id="test",
        channel="telegram",
        chat_id="chat-1",
        chat_type="private",
    )

    assert "Descripción de la escena" not in resultado.text_context
    assert "No se pudo" not in resultado.text_context
