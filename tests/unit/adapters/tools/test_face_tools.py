"""Tests para las 8 face tools."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock

import numpy as np
import pytest

from adapters.outbound.tools.face_tools import (
    AddPhotoToPersonTool,
    FindDuplicatePersonsTool,
    ForgetPersonTool,
    ListKnownPersonsTool,
    MergePersonsTool,
    RegisterFaceTool,
    SkipFaceTool,
    UpdatePersonMetadataTool,
)
from core.domain.entities.face import (
    BBox,
    FaceMatch,
    MatchStatus,
    MessageFaceMetadata,
    Person,
)
from core.domain.value_objects.channel_context import ChannelContext


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _telegram_ctx() -> ChannelContext:
    return ChannelContext(channel_type="telegram", user_id="chat-1")


def _build_metadata_with_embedding(
    face_idx: int = 0, embedding: np.ndarray | None = None
) -> MessageFaceMetadata:
    """Construye metadata con embedding serializado y face_match correspondiente."""
    if embedding is None:
        embedding = np.array([0.1] * 512, dtype=np.float32)
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **{str(face_idx): embedding})

    from datetime import datetime

    face_match = FaceMatch(
        face_ref=f"42#{face_idx}",
        bbox=BBox(x=0, y=0, w=10, h=10),
        candidates=[],
        status=MatchStatus.UNKNOWN,
    )
    return MessageFaceMetadata(
        history_id=42,
        agent_id="test",
        channel="telegram",
        chat_id="chat-1",
        faces=[face_match],
        embeddings_blob=buffer.getvalue(),
        created_at=datetime.utcnow(),
    )


# ======================================================================
# 1. RegisterFaceTool
# ======================================================================


async def test_register_face_happy_path():
    registry = AsyncMock()
    persona = Person(id="p-001", nombre="Alberto", embeddings_count=1)
    registry.register_person.return_value = persona

    metadata = _build_metadata_with_embedding(face_idx=0)
    metadata_repo = AsyncMock()
    metadata_repo.resolve_face_ref.return_value = (metadata, 0)

    tool = RegisterFaceTool(
        face_registry=registry,
        metadata_repo=metadata_repo,
        agent_id="test",
        get_channel_context=lambda: _telegram_ctx(),
    )

    result = await tool.execute(face_ref="42#0", nombre="Alberto", apellido="Hernández")

    assert result.success is True
    assert "p-001" in result.output
    registry.register_person.assert_called_once()
    call_kwargs = registry.register_person.call_args.kwargs
    assert call_kwargs["nombre"] == "Alberto"
    assert call_kwargs["apellido"] == "Hernández"
    # categoria no se pasa explícitamente → toma default None (persona normal)
    assert call_kwargs.get("categoria") is None


async def test_register_face_missing_params():
    tool = RegisterFaceTool(
        face_registry=AsyncMock(),
        metadata_repo=AsyncMock(),
        agent_id="test",
        get_channel_context=lambda: _telegram_ctx(),
    )
    result = await tool.execute(face_ref="42#0")  # no nombre
    assert result.success is False
    assert "required" in result.output.lower()


async def test_register_face_ref_not_found():
    registry = AsyncMock()
    metadata_repo = AsyncMock()
    metadata_repo.resolve_face_ref.return_value = None

    tool = RegisterFaceTool(
        face_registry=registry,
        metadata_repo=metadata_repo,
        agent_id="test",
        get_channel_context=lambda: _telegram_ctx(),
    )
    result = await tool.execute(face_ref="9999#0", nombre="Alguien")
    assert result.success is False
    assert "not found" in result.output.lower()


async def test_register_face_no_channel_context():
    tool = RegisterFaceTool(
        face_registry=AsyncMock(),
        metadata_repo=AsyncMock(),
        agent_id="test",
        get_channel_context=lambda: None,
    )
    result = await tool.execute(face_ref="42#0", nombre="X")
    assert result.success is False
    assert "conversation" in result.output.lower()


# ======================================================================
# 2. AddPhotoToPersonTool
# ======================================================================


async def test_add_photo_to_person_happy_path():
    registry = AsyncMock()
    persona = Person(id="p-001", nombre="Alberto")
    registry.get_person.return_value = persona
    metadata = _build_metadata_with_embedding(face_idx=0)
    metadata_repo = AsyncMock()
    metadata_repo.resolve_face_ref.return_value = (metadata, 0)

    tool = AddPhotoToPersonTool(
        face_registry=registry,
        metadata_repo=metadata_repo,
        agent_id="test",
        get_channel_context=lambda: _telegram_ctx(),
    )

    result = await tool.execute(person_id="p-001", face_ref="42#0")

    assert result.success is True
    registry.add_embedding_to_person.assert_called_once()
    args = registry.add_embedding_to_person.call_args.kwargs
    assert args["person_id"] == "p-001"
    assert args["source_face_ref"] == "42#0"


# ======================================================================
# 3. UpdatePersonMetadataTool
# ======================================================================


async def test_update_person_metadata_happy_path():
    registry = AsyncMock()
    persona = Person(id="p-001", nombre="Alberto")
    persona_actualizada = Person(
        id="p-001", nombre="Alberto", apellido="Hernández", relacion="dueño"
    )
    registry.get_person.return_value = persona
    registry.update_person_metadata.return_value = persona_actualizada

    tool = UpdatePersonMetadataTool(face_registry=registry)
    result = await tool.execute(person_id="p-001", relacion="dueño")

    assert result.success is True
    registry.update_person_metadata.assert_called_once_with("p-001", relacion="dueño")


async def test_update_person_metadata_no_fields():
    tool = UpdatePersonMetadataTool(face_registry=AsyncMock())
    result = await tool.execute(person_id="p-001")  # solo el id, nada que actualizar
    assert result.success is False
    assert "no fields" in result.output.lower()


# ======================================================================
# 4. ListKnownPersonsTool
# ======================================================================


async def test_list_known_persons_with_persons():
    registry = AsyncMock()
    registry.list_persons.return_value = [
        Person(id="p-001", nombre="Alberto", apellido="Hernández", relacion="dueño"),
        Person(id="p-002", nombre="Maël", relacion="hijo"),
    ]

    tool = ListKnownPersonsTool(face_registry=registry)
    result = await tool.execute()

    assert result.success is True
    assert "Alberto" in result.output
    assert "Maël" in result.output
    registry.list_persons.assert_called_once_with(incluir_ignoradas=False)


async def test_list_known_persons_empty():
    registry = AsyncMock()
    registry.list_persons.return_value = []

    tool = ListKnownPersonsTool(face_registry=registry)
    result = await tool.execute()

    assert result.success is True
    assert "No known persons" in result.output


async def test_list_known_persons_incluir_ignoradas():
    registry = AsyncMock()
    registry.list_persons.return_value = [
        Person(id="p-001", nombre="Alberto"),
        Person(id="p-bg-1", nombre=None, categoria="ignorada"),
    ]

    tool = ListKnownPersonsTool(face_registry=registry)
    result = await tool.execute(incluir_ignoradas=True)

    assert result.success is True
    assert "[ignorada]" in result.output
    registry.list_persons.assert_called_once_with(incluir_ignoradas=True)


# ======================================================================
# 5. ForgetPersonTool
# ======================================================================


async def test_forget_person_happy_path():
    registry = AsyncMock()
    tool = ForgetPersonTool(face_registry=registry)
    result = await tool.execute(person_id="p-001")

    assert result.success is True
    assert "forgotten" in result.output.lower()
    registry.forget_person.assert_called_once_with("p-001")


# ======================================================================
# 6. SkipFaceTool
# ======================================================================


async def test_skip_face_persists_with_ignorada_categoria():
    registry = AsyncMock()
    persona_ignorada = Person(id="p-ign-1", nombre=None, categoria="ignorada")
    registry.register_person.return_value = persona_ignorada

    metadata = _build_metadata_with_embedding(face_idx=2)
    metadata_repo = AsyncMock()
    metadata_repo.resolve_face_ref.return_value = (metadata, 2)

    tool = SkipFaceTool(
        face_registry=registry,
        metadata_repo=metadata_repo,
        agent_id="test",
        get_channel_context=lambda: _telegram_ctx(),
    )
    result = await tool.execute(face_ref="42#2")

    assert result.success is True
    registry.register_person.assert_called_once()
    args = registry.register_person.call_args.kwargs
    assert args["nombre"] is None
    assert args["categoria"] == "ignorada"
    assert args["source_face_ref"] == "42#2"


# ======================================================================
# 7. MergePersonsTool
# ======================================================================


async def test_merge_persons_happy_path():
    registry = AsyncMock()
    persona_target = Person(id="p-target", nombre="Alberto", embeddings_count=5)
    registry.merge_persons.return_value = persona_target

    tool = MergePersonsTool(face_registry=registry)
    result = await tool.execute(source_id="p-source", target_id="p-target")

    assert result.success is True
    assert "5" in result.output
    registry.merge_persons.assert_called_once_with("p-source", "p-target")


async def test_merge_persons_same_id_rejected():
    tool = MergePersonsTool(face_registry=AsyncMock())
    result = await tool.execute(source_id="p-001", target_id="p-001")

    assert result.success is False
    assert "same" in result.output.lower()


# ======================================================================
# 8. FindDuplicatePersonsTool
# ======================================================================


async def test_find_duplicate_persons_finds_pair_above_threshold():
    """Dos personas con centroides idénticos → similitud 1.0 → reportadas."""
    registry = AsyncMock()
    registry.list_persons.return_value = [
        Person(id="p-001", nombre="Alberto"),
        Person(id="p-002", nombre="Albert"),
        Person(id="p-003", nombre="Carlos"),  # tercero, no duplicado
    ]
    centroide_albertos = np.array([1.0] + [0.0] * 511, dtype=np.float32)
    centroide_carlos = np.array([0.0, 1.0] + [0.0] * 510, dtype=np.float32)
    centroides_map = {
        "p-001": centroide_albertos,
        "p-002": centroide_albertos,
        "p-003": centroide_carlos,
    }
    registry.get_centroid.side_effect = lambda pid: centroides_map.get(pid)

    tool = FindDuplicatePersonsTool(face_registry=registry, default_threshold=0.70)
    result = await tool.execute()

    assert result.success is True
    assert "p-001" in result.output
    assert "p-002" in result.output
    assert "p-003" not in result.output  # Carlos no es duplicado de los Albertos


async def test_find_duplicate_persons_no_duplicates():
    """Solo 1 persona → no hay nada que comparar."""
    registry = AsyncMock()
    registry.list_persons.return_value = [Person(id="p-001", nombre="Alberto")]

    tool = FindDuplicatePersonsTool(face_registry=registry)
    result = await tool.execute()

    assert result.success is True
    assert "Less than 2" in result.output


async def test_find_duplicate_persons_below_threshold():
    """Centroides ortogonales → similitud 0 → ningún duplicado."""
    registry = AsyncMock()
    registry.list_persons.return_value = [
        Person(id="p-001", nombre="Alberto"),
        Person(id="p-002", nombre="Carlos"),
    ]
    registry.get_centroid.side_effect = lambda pid: (
        np.array([1.0] + [0.0] * 511, dtype=np.float32)
        if pid == "p-001"
        else np.array([0.0, 1.0] + [0.0] * 510, dtype=np.float32)
    )

    tool = FindDuplicatePersonsTool(face_registry=registry, default_threshold=0.70)
    result = await tool.execute()

    assert result.success is True
    assert "No duplicate candidates" in result.output
