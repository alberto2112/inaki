"""Tests para las entidades de dominio del reconocimiento facial.

Cubre: Person, BBox, FaceDetection, MatchStatus, FaceMatch, MessageFaceMetadata.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from core.domain.entities.face import (
    BBox,
    FaceDetection,
    FaceMatch,
    MatchStatus,
    MessageFaceMetadata,
    Person,
)


# ---------------------------------------------------------------------------
# Person
# ---------------------------------------------------------------------------


class TestPerson:
    def test_persona_con_todos_los_campos(self) -> None:
        persona = Person(
            nombre="Alberto",
            apellido="García",
            fecha_nacimiento="1990-05-15",
            relacion="dueño",
            notes="Persona de prueba",
        )
        assert persona.nombre == "Alberto"
        assert persona.apellido == "García"
        assert persona.relacion == "dueño"

    def test_persona_nombre_nullable(self) -> None:
        """nombre=None es válido — se usa para personas ignoradas."""
        persona = Person(nombre=None)
        assert persona.nombre is None

    def test_persona_ignorada_con_categoria(self) -> None:
        """Persona ignorada tiene categoria='ignorada' y nombre=None."""
        persona = Person(nombre=None, categoria="ignorada")
        assert persona.nombre is None
        assert persona.categoria == "ignorada"

    def test_persona_normal_categoria_none(self) -> None:
        """Persona normal tiene categoria=None por defecto."""
        persona = Person(nombre="Iñaki")
        assert persona.categoria is None

    def test_persona_id_generado_automaticamente(self) -> None:
        persona = Person(nombre="Test")
        assert persona.id is not None
        # Debe ser un UUID válido
        uuid.UUID(persona.id)

    def test_persona_ids_unicos(self) -> None:
        p1 = Person(nombre="A")
        p2 = Person(nombre="B")
        assert p1.id != p2.id

    def test_persona_embeddings_count_default_cero(self) -> None:
        persona = Person(nombre="Test")
        assert persona.embeddings_count == 0

    def test_persona_created_at_se_genera(self) -> None:
        persona = Person(nombre="Test")
        assert isinstance(persona.created_at, datetime)

    def test_persona_serializable_json(self) -> None:
        """Round-trip JSON sin pérdida de campos."""
        persona = Person(nombre="Alberto", categoria=None)
        json_str = persona.model_dump_json()
        persona2 = Person.model_validate_json(json_str)
        assert persona2.nombre == persona.nombre
        assert persona2.id == persona.id
        assert persona2.categoria == persona.categoria

    def test_persona_ignorada_serializable_json(self) -> None:
        """Persona ignorada sobrevive round-trip JSON."""
        persona = Person(nombre=None, categoria="ignorada")
        json_str = persona.model_dump_json()
        persona2 = Person.model_validate_json(json_str)
        assert persona2.nombre is None
        assert persona2.categoria == "ignorada"


# ---------------------------------------------------------------------------
# BBox
# ---------------------------------------------------------------------------


class TestBBox:
    def test_bbox_campos_correctos(self) -> None:
        bbox = BBox(x=10, y=20, w=100, h=150)
        assert bbox.x == 10
        assert bbox.y == 20
        assert bbox.w == 100
        assert bbox.h == 150

    def test_bbox_serializable(self) -> None:
        bbox = BBox(x=0, y=0, w=50, h=50)
        data = bbox.model_dump()
        assert data == {"x": 0, "y": 0, "w": 50, "h": 50}


# ---------------------------------------------------------------------------
# FaceDetection
# ---------------------------------------------------------------------------


class TestFaceDetection:
    def test_face_detection_campos_correctos(self) -> None:
        emb = [0.1] * 512
        bbox = BBox(x=10, y=20, w=100, h=150)
        det = FaceDetection(bbox=bbox, embedding=emb, detection_score=0.95)
        assert det.embedding == emb
        assert det.detection_score == 0.95
        assert det.bbox == bbox

    def test_face_detection_embedding_es_lista_de_floats(self) -> None:
        emb = [float(i) for i in range(512)]
        det = FaceDetection(
            bbox=BBox(x=0, y=0, w=1, h=1),
            embedding=emb,
            detection_score=0.8,
        )
        assert len(det.embedding) == 512
        assert all(isinstance(v, float) for v in det.embedding)


# ---------------------------------------------------------------------------
# MatchStatus
# ---------------------------------------------------------------------------


class TestMatchStatus:
    def test_match_status_valores(self) -> None:
        assert MatchStatus.MATCHED == "matched"
        assert MatchStatus.AMBIGUOUS == "ambiguous"
        assert MatchStatus.UNKNOWN == "unknown"

    def test_match_status_es_str(self) -> None:
        assert isinstance(MatchStatus.MATCHED, str)


# ---------------------------------------------------------------------------
# FaceMatch
# ---------------------------------------------------------------------------


class TestFaceMatch:
    def _persona(self, nombre: str = "Test", categoria: str | None = None) -> Person:
        return Person(nombre=nombre, categoria=categoria)

    def _bbox(self) -> BBox:
        return BBox(x=10, y=20, w=100, h=150)

    def test_face_match_matched(self) -> None:
        persona = self._persona("Alberto")
        fm = FaceMatch(
            face_ref="4231#0",
            bbox=self._bbox(),
            candidates=[(persona, 0.82)],
            status=MatchStatus.MATCHED,
            categoria=None,
        )
        assert fm.status == MatchStatus.MATCHED
        assert fm.candidates[0][0].nombre == "Alberto"

    def test_face_ref_formato_history_id_hash_idx(self) -> None:
        """face_ref debe tener formato '{history_id}#{idx}'."""
        fm = FaceMatch(
            face_ref="4231#0",
            bbox=self._bbox(),
            candidates=[],
            status=MatchStatus.UNKNOWN,
        )
        partes = fm.face_ref.split("#")
        assert len(partes) == 2
        assert partes[0] == "4231"
        assert partes[1] == "0"

    def test_face_match_con_persona_ignorada(self) -> None:
        """FaceMatch puede tener categoria='ignorada' cuando el mejor candidato es ignorado."""
        persona_ignorada = self._persona(nombre=None, categoria="ignorada")
        fm = FaceMatch(
            face_ref="100#1",
            bbox=self._bbox(),
            candidates=[(persona_ignorada, 0.75)],
            status=MatchStatus.MATCHED,
            categoria="ignorada",
        )
        assert fm.categoria == "ignorada"
        assert fm.candidates[0][0].nombre is None

    def test_face_match_categoria_none_por_defecto(self) -> None:
        fm = FaceMatch(
            face_ref="1#0",
            bbox=self._bbox(),
            candidates=[],
            status=MatchStatus.UNKNOWN,
        )
        assert fm.categoria is None

    def test_face_match_multiples_candidatos(self) -> None:
        p1 = self._persona("Alberto")
        p2 = self._persona("Iñaki")
        fm = FaceMatch(
            face_ref="99#0",
            bbox=self._bbox(),
            candidates=[(p1, 0.82), (p2, 0.45)],
            status=MatchStatus.MATCHED,
        )
        assert len(fm.candidates) == 2
        assert fm.candidates[0][1] > fm.candidates[1][1]  # ordenados desc

    def test_face_match_serializable_json(self) -> None:
        """FaceMatch sobrevive round-trip JSON."""
        persona = self._persona("Alberto")
        fm = FaceMatch(
            face_ref="4231#0",
            bbox=self._bbox(),
            candidates=[(persona, 0.82)],
            status=MatchStatus.MATCHED,
        )
        json_str = fm.model_dump_json()
        fm2 = FaceMatch.model_validate_json(json_str)
        assert fm2.face_ref == "4231#0"
        assert fm2.status == MatchStatus.MATCHED
        assert fm2.candidates[0][1] == pytest.approx(0.82)


# ---------------------------------------------------------------------------
# MessageFaceMetadata
# ---------------------------------------------------------------------------


class TestMessageFaceMetadata:
    def _face_match(self, face_ref: str = "1#0") -> FaceMatch:
        return FaceMatch(
            face_ref=face_ref,
            bbox=BBox(x=0, y=0, w=50, h=50),
            candidates=[],
            status=MatchStatus.UNKNOWN,
        )

    def test_message_face_metadata_construccion(self) -> None:
        metadata = MessageFaceMetadata(
            history_id=42,
            agent_id="general",
            channel="telegram",
            chat_id="12345",
            faces=[self._face_match("42#0"), self._face_match("42#1")],
            embeddings_blob=b"\x00\x01\x02",
            created_at=datetime.utcnow(),
        )
        assert metadata.history_id == 42
        assert metadata.agent_id == "general"
        assert len(metadata.faces) == 2

    def test_message_face_metadata_round_trip_dict(self) -> None:
        """MessageFaceMetadata sobrevive round-trip dict (para almacenamiento en DB)."""
        blob = b"\xde\xad\xbe\xef"
        metadata = MessageFaceMetadata(
            history_id=100,
            agent_id="test",
            channel="telegram",
            chat_id="999",
            faces=[self._face_match("100#0")],
            embeddings_blob=blob,
            created_at=datetime.utcnow(),
        )
        data = metadata.model_dump()
        metadata2 = MessageFaceMetadata.model_validate(data)
        assert metadata2.history_id == 100
        assert metadata2.agent_id == "test"
        assert len(metadata2.faces) == 1
        assert metadata2.faces[0].face_ref == "100#0"
        # embeddings_blob sobrevive como bytes
        assert metadata2.embeddings_blob == blob

    def test_message_face_metadata_faces_vacias(self) -> None:
        """faces=[] es válido (foto sin caras detectadas)."""
        metadata = MessageFaceMetadata(
            history_id=1,
            agent_id="test",
            channel="telegram",
            chat_id="0",
            faces=[],
            embeddings_blob=b"",
            created_at=datetime.utcnow(),
        )
        assert metadata.faces == []
