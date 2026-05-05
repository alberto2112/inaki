"""Caso de uso para procesar una foto entrante via Telegram.

Orquesta visión + registro facial + descripción de escena + anotación + metadata.
Produce un texto contextual en español que el agente principal recibe como
``user_input`` adicional, y opcionalmente una imagen anotada para enviar al usuario.

Hexagonal: solo importa de ``core/ports/`` y ``core/domain/``. Nunca de adapters/.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Protocol

import numpy as np

from core.domain.entities.face import (
    FaceDetection,
    FaceMatch,
    MatchStatus,
    MessageFaceMetadata,
    Person,
    ProcessPhotoResult,
)
from core.ports.outbound.face_registry_port import IFaceRegistryPort
from core.ports.outbound.message_face_metadata_port import IMessageFaceMetadataRepo
from core.ports.outbound.scene_describer_port import ISceneDescriberPort
from core.ports.outbound.vision_port import IVisionPort
from infrastructure.config import PhotosConfig

logger = logging.getLogger(__name__)


class IPhotoAnnotator(Protocol):
    """Protocolo duck-typed para el anotador. Lo implementa PillowPhotoAnnotator."""

    def draw_numbered(
        self, image_bytes: bytes, caras: list[FaceMatch]
    ) -> bytes:  # pragma: no cover
        ...


class ProcessPhotoUseCase:
    """Procesa una foto entrante: detecta caras, identifica personas, describe escena.

    Devuelve un texto en español que se inyecta como contexto al agente principal,
    más opcionalmente una imagen anotada para enviar al usuario cuando hay caras
    desconocidas en chat privado.

    Se llama desde el adaptador inbound de Telegram (``filters.PHOTO`` handler).
    El adaptador, después de recibir el resultado, llama a ``run_agent.execute()``
    con ``user_input=text_context``.
    """

    def __init__(
        self,
        vision: IVisionPort,
        face_registry: IFaceRegistryPort,
        scene_describer: ISceneDescriberPort | None,
        annotator: IPhotoAnnotator,
        metadata_repo: IMessageFaceMetadataRepo,
        config: PhotosConfig,
    ) -> None:
        self._vision = vision
        self._face_registry = face_registry
        self._scene_describer = scene_describer
        self._annotator = annotator
        self._metadata_repo = metadata_repo
        self._config = config

    async def execute(
        self,
        image_bytes: bytes,
        history_id: int,
        agent_id: str,
        channel: str,
        chat_id: str,
        chat_type: str,
        analysis_only: bool = False,
        scene_prompt: str | None = None,
    ) -> ProcessPhotoResult:
        """Procesa una foto entrante y devuelve contexto para el agente.

        Args:
            image_bytes: Bytes de la foto descargada (JPEG/PNG).
            history_id: ID del mensaje de la foto en history.db (ya persistido).
            agent_id: ID del agente que recibe la foto.
            channel: Canal del mensaje (ej: 'telegram').
            chat_id: ID del chat dentro del canal.
            chat_type: Tipo de chat: 'private', 'group', 'supergroup', 'channel'.
            analysis_only: Si True (foto con caption), suprime la imagen anotada y
                las sugerencias de enrollment. El agente recibe reconocimiento de
                personas y escena, pero sin face_refs ni prompts de registro.

        Returns:
            ProcessPhotoResult con texto contextual, imagen anotada opcional, y
            flag ``should_skip_run_agent`` para indicar al adapter que omita el
            ciclo del agente (ej: cuando photos.enabled=False).
        """
        # 1. Feature deshabilitada
        if not self._config.enabled:
            return ProcessPhotoResult(
                text_context="",
                annotated_image=None,
                should_skip_run_agent=True,
            )

        # 2. Detectar caras + embeddings
        detections = await self._vision.detect_and_embed(image_bytes)

        # 3. Construir FaceMatch por cara con status según thresholds
        face_matches = await self._construir_face_matches(detections, history_id)

        # 4. Filtrar y categorizar para output
        es_privado = (
            chat_type == "private" and self._config.enrollment_chats == "private"
        )
        desconocidas = [
            fm
            for fm in face_matches
            if fm.status in (MatchStatus.UNKNOWN, MatchStatus.AMBIGUOUS)
        ]

        # 5. Imagen anotada (solo en chat privado con caras desconocidas/ambiguas,
        #    y solo cuando el usuario NO mandó caption — analysis_only suprime el enrollment).
        annotated_image: bytes | None = None
        if es_privado and desconocidas and not analysis_only:
            annotated_image = self._annotator.draw_numbered(image_bytes, desconocidas)

        # 6. Descripción de escena (siempre que haya describer; degrade graceful)
        scene_description = await self._describir_escena(image_bytes, prompt=scene_prompt)

        # 7. Construir contexto textual en español
        text_context = self._construir_contexto(
            face_matches=face_matches,
            scene_description=scene_description,
            es_privado=es_privado,
            analysis_only=analysis_only,
        )

        # 8. Persistir metadata (incluye ignoradas para auditoría)
        if face_matches:
            await self._persistir_metadata(
                detections=detections,
                face_matches=face_matches,
                history_id=history_id,
                agent_id=agent_id,
                channel=channel,
                chat_id=chat_id,
            )

        debug_path: str | None = None
        if self._config.debug:
            debug_path = self._write_debug_phase1(
                image_bytes=image_bytes,
                detections=detections,
                face_matches=face_matches,
                scene_description=scene_description,
                text_context=text_context,
                chat_type=chat_type,
                channel=channel,
                chat_id=chat_id,
                agent_id=agent_id,
            )

        return ProcessPhotoResult(
            text_context=text_context,
            annotated_image=annotated_image,
            should_skip_run_agent=False,
            debug_path=debug_path,
        )

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _write_debug_phase1(
        self,
        *,
        image_bytes: bytes,
        detections: list,
        face_matches: list,
        scene_description: str | None,
        text_context: str,
        chat_type: str,
        channel: str,
        chat_id: str,
        agent_id: str,
    ) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = f"/tmp/inaki.photo-debug.{ts}.log"
        lines: list[str] = [
            "=== IÑAKI PHOTO DEBUG ===",
            f"Timestamp: {datetime.now().isoformat()}",
            f"Agent: {agent_id}",
            f"Chat: channel={channel}, chat_id={chat_id}, chat_type={chat_type}",
            f"Config: enabled={self._config.enabled}, enrollment_chats={self._config.enrollment_chats}",
            "",
            "--- Fase 1: ProcessPhotoUseCase ---",
            f"Imagen: {len(image_bytes)} bytes",
            f"Detecciones InsightFace: {len(detections)}",
        ]
        if face_matches:
            lines.append("Face matches:")
            for fm in face_matches:
                top = ""
                if fm.candidates:
                    persona, score = fm.candidates[0]
                    nombre = self._formatear_nombre(persona)
                    top = f" top={nombre} ({score:.3f})"
                lines.append(f"  {fm.face_ref}  status={fm.status.value}{top}  categoria={fm.categoria!r}")
        else:
            lines.append("Face matches: (ninguno)")
        lines += [
            "",
            "--- Descripción de escena ---",
            scene_description if scene_description is not None else "(scene describer no configurado o falló)",
            "",
            "--- text_context (salida de Phase 1, user_input del agente) ---",
            text_context,
            "",
        ]
        try:
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            logger.debug("photo-debug Phase 1 escrito en %s", path)
        except OSError as exc:
            logger.warning("No se pudo escribir photo-debug Phase 1: %s", exc)
            return ""
        return path

    async def _construir_face_matches(
        self,
        detections: list[FaceDetection],
        history_id: int,
    ) -> list[FaceMatch]:
        """Construye un FaceMatch por cara detectada, aplicando thresholds del config.

        ``find_matches`` del registro devuelve un FaceMatch por candidato KNN
        (placeholder face_ref/bbox). Acá los consolidamos en un FaceMatch por
        cara con todos los candidatos juntos y status calculado.
        """
        match_threshold = self._config.faces.match_threshold
        ambiguous_threshold = self._config.faces.ambiguous_threshold

        face_matches: list[FaceMatch] = []
        for idx, detection in enumerate(detections):
            face_ref = f"{history_id}#{idx}"
            embedding_np = np.asarray(detection.embedding, dtype=np.float32)

            candidatos_raw = await self._face_registry.find_matches(
                embedding=embedding_np, k=3
            )
            candidatos: list[tuple[Person, float]] = []
            for fm in candidatos_raw:
                if fm.candidates:
                    candidatos.append(fm.candidates[0])

            # Status según top candidato
            if candidatos:
                top_persona, top_score = candidatos[0]
                top_categoria = top_persona.categoria
                if top_score >= match_threshold:
                    status = MatchStatus.MATCHED
                elif top_score >= ambiguous_threshold:
                    status = MatchStatus.AMBIGUOUS
                else:
                    status = MatchStatus.UNKNOWN
            else:
                top_categoria = None
                status = MatchStatus.UNKNOWN

            face_matches.append(
                FaceMatch(
                    face_ref=face_ref,
                    bbox=detection.bbox,
                    candidates=candidatos,
                    status=status,
                    categoria=top_categoria,
                )
            )
        return face_matches

    async def _describir_escena(self, image_bytes: bytes, prompt: str | None = None) -> str | None:
        """Llama al describer si está configurado. Degrade graceful en error."""
        if self._scene_describer is None:
            return None
        try:
            return await self._scene_describer.describe_image(image_bytes, prompt=prompt)
        except Exception as exc:  # noqa: BLE001 — degrade graceful intencional
            logger.warning("Falló la descripción de escena: %s", exc)
            return None

    def _construir_contexto(
        self,
        *,
        face_matches: list[FaceMatch],
        scene_description: str | None,
        es_privado: bool,
        analysis_only: bool = False,
    ) -> str:
        """Construye el texto contextual en español para el agente.

        Lista unificada de caras ordenada por idx (igual que la imagen anotada)
        para que el agente nunca confunda qué número corresponde a qué persona.
        El sender se antepone en el adapter de Telegram con el prefijo
        ``{sender} (foto): ...`` en grupos — acá no se incluye encabezado.
        """
        secciones: list[str] = []

        visibles = [fm for fm in face_matches if fm.categoria != "ignorada"]
        visibles.sort(key=lambda fm: self._idx_de_face_ref(fm.face_ref))

        if visibles:
            hay_no_identificadas = any(
                fm.status in (MatchStatus.UNKNOWN, MatchStatus.AMBIGUOUS) for fm in visibles
            )
            lineas = [f"Caras detectadas ({len(visibles)}):"]
            for fm in visibles:
                idx_str = fm.face_ref.split("#")[-1] if "#" in fm.face_ref else fm.face_ref
                if fm.status == MatchStatus.MATCHED and fm.candidates:
                    top_persona, top_score = fm.candidates[0]
                    nombre = self._formatear_nombre(top_persona)
                    lineas.append(f"- Cara [{idx_str}]: {nombre} — reconocida (similitud {top_score:.2f})")
                elif fm.status == MatchStatus.AMBIGUOUS and fm.candidates:
                    top_persona, top_score = fm.candidates[0]
                    nombre = self._formatear_nombre(top_persona)
                    if es_privado and not analysis_only:
                        lineas.append(
                            f"- Cara [{idx_str}]: posible match {nombre} (similitud {top_score:.2f}, no confirmado)"
                            f" — face_ref: {fm.face_ref}. Podés usar add_photo_to_person para reforzar."
                        )
                    else:
                        lineas.append(f"- Cara [{idx_str}]: posible match {nombre} (similitud {top_score:.2f}, no confirmado)")
                else:
                    if es_privado and not analysis_only:
                        lineas.append(f"- Cara [{idx_str}]: desconocida — face_ref: {fm.face_ref}")
                    else:
                        lineas.append(f"- Cara [{idx_str}]: desconocida")

            if hay_no_identificadas and not es_privado:
                lineas.append("(El registro de nuevas personas solo está disponible en chat privado.)")
            elif hay_no_identificadas and es_privado and not analysis_only:
                lineas.append("Las caras no identificadas están numeradas en la imagen anotada.")

            secciones.append("\n".join(lineas))

        if scene_description is not None:
            secciones.append(f"Descripción de la escena:\n{scene_description}")
        elif self._scene_describer is not None:
            secciones.append("No se pudo obtener descripción de escena.")
        elif not visibles:
            secciones.append("(Sin descripción de escena configurada y sin caras detectadas.)")

        return "\n\n".join(secciones)

    @staticmethod
    def _idx_de_face_ref(face_ref: str) -> int:
        try:
            return int(face_ref.split("#")[-1])
        except (ValueError, IndexError):
            return 0

    @staticmethod
    def _formatear_nombre(persona: Person) -> str:
        """Formatea el nombre completo de una persona para mostrar al usuario."""
        partes: list[str] = []
        if persona.nombre:
            partes.append(persona.nombre)
        if persona.apellido:
            partes.append(persona.apellido)
        return " ".join(partes) if partes else "(sin nombre)"

    async def _persistir_metadata(
        self,
        *,
        detections: list[FaceDetection],
        face_matches: list[FaceMatch],
        history_id: int,
        agent_id: str,
        channel: str,
        chat_id: str,
    ) -> None:
        """Serializa embeddings y guarda metadata para resolver face_ref después."""
        embeddings_blob = self._serializar_embeddings(detections)
        await self._metadata_repo.save(
            MessageFaceMetadata(
                history_id=history_id,
                agent_id=agent_id,
                channel=channel,
                chat_id=chat_id,
                faces=face_matches,
                embeddings_blob=embeddings_blob,
                created_at=datetime.utcnow(),
            )
        )

    @staticmethod
    def _serializar_embeddings(detections: list[FaceDetection]) -> bytes:
        """Serializa todos los embeddings como numpy savez_compressed."""
        if not detections:
            return b""
        arrays = {
            str(idx): np.asarray(d.embedding, dtype=np.float32)
            for idx, d in enumerate(detections)
        }
        buffer = io.BytesIO()
        np.savez_compressed(buffer, **arrays)
        return buffer.getvalue()
