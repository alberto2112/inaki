"""Tools de gestión del registro facial (faces.db).

Estas tools son usadas por el LLM para gestionar el registro de personas conocidas
mediante interacciones conversacionales o explícitas via Telegram.

7 tools de enrollment + utilities:
- register_face / skip_face: registran una cara nueva en faces.db
- add_photo_to_person: suma un embedding a una persona existente
- update_person_metadata: actualiza campos (nombre, fecha_nacimiento, etc.)
- list_known_persons: lista las personas registradas
- forget_person: borra a una persona (privacidad)
- merge_persons: fusiona dos personas (manual o desde dedup nocturno)

Plus 1 tool para el dedup nocturno:
- find_duplicate_persons: detecta pares de personas con embeddings similares

Wiring: estas tools NO son extensiones (necesitan deps inyectadas — tienen
constructor con argumentos). Se registran en `_register_tools()` del AgentContainer.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable
from typing import Any

import numpy as np

from core.domain.entities.face import Person
from core.domain.value_objects.channel_context import ChannelContext
from core.ports.outbound.face_registry_port import IFaceRegistryPort
from core.ports.outbound.message_face_metadata_port import IMessageFaceMetadataRepo
from core.ports.outbound.tool_port import ITool, ToolResult

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Helper compartido: resolución de face_ref → embedding
# ----------------------------------------------------------------------


async def _resolver_embedding_de_face_ref(
    face_ref: str,
    *,
    agent_id: str,
    channel: str,
    chat_id: str,
    metadata_repo: IMessageFaceMetadataRepo,
) -> tuple[np.ndarray, int] | None:
    """Resuelve un face_ref al par (embedding numpy, history_id).

    Devuelve None si el face_ref no se encuentra (mensaje muy viejo, o nunca
    existió, o pertenece a otro thread).

    Raises:
        ValueError: Si el formato del face_ref es inválido.
    """
    resultado = await metadata_repo.resolve_face_ref(
        agent_id=agent_id,
        channel=channel,
        chat_id=chat_id,
        face_ref=face_ref,
    )
    if resultado is None:
        return None

    metadata, face_idx = resultado
    if not metadata.embeddings_blob:
        return None

    # Deserializar el blob (numpy savez_compressed)
    buffer = io.BytesIO(metadata.embeddings_blob)
    archivo = np.load(buffer)
    clave = str(face_idx)
    if clave not in archivo:
        return None
    return archivo[clave], metadata.history_id


def _formatear_persona_breve(p: Person) -> str:
    """Formatea una persona para mostrar al LLM. Marca [ignorada] si aplica."""
    if p.categoria == "ignorada":
        return f"{p.id} [ignorada]"
    nombre = p.nombre or "(sin nombre)"
    apellido = f" {p.apellido}" if p.apellido else ""
    relacion = f" — {p.relacion}" if p.relacion else ""
    return f"{p.id}: {nombre}{apellido}{relacion}"


def _resolver_chat_context(
    get_channel_context: Callable[[], ChannelContext | None],
) -> ChannelContext | None:
    """Wrapper defensivo: el callable puede devolver None fuera de conversación."""
    try:
        return get_channel_context()
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------------
# 1. RegisterFaceTool
# ----------------------------------------------------------------------


class RegisterFaceTool(ITool):
    name = "register_face"
    description = (
        "Register a newly detected face in the photo registry as a known person. "
        "Required: 'face_ref' (the reference token like '4231#0' shown in the "
        "annotated photo's metadata). "
        "Required: 'nombre' (first name). "
        "Optional: 'apellido', 'fecha_nacimiento' (ISO YYYY-MM-DD), 'relacion' "
        "(free-form, e.g. 'hijo', 'amigo', 'colega'). "
        "Use this when the user identifies an unknown face by name."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "face_ref": {
                "type": "string",
                "description": "Face reference token like '{history_id}#{idx}'.",
            },
            "nombre": {"type": "string", "description": "First name (required)."},
            "apellido": {"type": "string", "description": "Last name (optional)."},
            "fecha_nacimiento": {
                "type": "string",
                "description": "Birthdate ISO YYYY-MM-DD (optional).",
            },
            "relacion": {
                "type": "string",
                "description": "Relationship/role (optional).",
            },
        },
        "required": ["face_ref", "nombre"],
    }

    def __init__(
        self,
        face_registry: IFaceRegistryPort,
        metadata_repo: IMessageFaceMetadataRepo,
        agent_id: str,
        get_channel_context: Callable[[], ChannelContext | None],
    ) -> None:
        self._registry = face_registry
        self._metadata_repo = metadata_repo
        self._agent_id = agent_id
        self._get_channel_context = get_channel_context

    async def execute(self, **kwargs: Any) -> ToolResult:
        face_ref = str(kwargs.get("face_ref") or "").strip()
        nombre = str(kwargs.get("nombre") or "").strip()
        if not face_ref or not nombre:
            return ToolResult(
                tool_name=self.name,
                output="Both 'face_ref' and 'nombre' are required.",
                success=False,
                error="missing required parameters",
                retryable=False,
            )

        ctx = _resolver_chat_context(self._get_channel_context)
        if ctx is None:
            return ToolResult(
                tool_name=self.name,
                output="register_face only works inside an active conversation.",
                success=False,
                error="no channel context",
                retryable=False,
            )

        resolved = await _resolver_embedding_de_face_ref(
            face_ref,
            agent_id=self._agent_id,
            channel=ctx.channel_type,
            chat_id=ctx.user_id,
            metadata_repo=self._metadata_repo,
        )
        if resolved is None:
            return ToolResult(
                tool_name=self.name,
                output=f"face_ref '{face_ref}' not found in recent thread metadata.",
                success=False,
                error="face_ref not found",
                retryable=False,
            )

        embedding, source_history_id = resolved

        try:
            persona = await self._registry.register_person(
                nombre=nombre,
                apellido=kwargs.get("apellido") or None,
                fecha_nacimiento=kwargs.get("fecha_nacimiento") or None,
                relacion=kwargs.get("relacion") or None,
                embedding=embedding,
                source_history_id=source_history_id,
                source_face_ref=face_ref,
            )
        except Exception as exc:
            logger.exception("RegisterFaceTool: error registrando %s", nombre)
            return ToolResult(
                tool_name=self.name,
                output=f"Error registering person: {exc}",
                success=False,
                error=str(exc),
                retryable=True,
            )

        return ToolResult(
            tool_name=self.name,
            output=f"Person '{nombre}' registered with id={persona.id}.",
            success=True,
        )


# ----------------------------------------------------------------------
# 2. AddPhotoToPersonTool
# ----------------------------------------------------------------------


class AddPhotoToPersonTool(ITool):
    name = "add_photo_to_person"
    description = (
        "Add another face embedding to an existing known person, improving future "
        "recognition robustness. "
        "Required: 'person_id' (the existing person's UUID), "
        "'face_ref' (the reference of a face detected in a recent photo). "
        "Use this when the same known person is detected with low confidence, or "
        "when the user explicitly asks to 'enseñar' another photo of someone."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "person_id": {"type": "string", "description": "Existing person's UUID."},
            "face_ref": {
                "type": "string",
                "description": "Face reference '{history_id}#{idx}' from a recent photo.",
            },
        },
        "required": ["person_id", "face_ref"],
    }

    def __init__(
        self,
        face_registry: IFaceRegistryPort,
        metadata_repo: IMessageFaceMetadataRepo,
        agent_id: str,
        get_channel_context: Callable[[], ChannelContext | None],
    ) -> None:
        self._registry = face_registry
        self._metadata_repo = metadata_repo
        self._agent_id = agent_id
        self._get_channel_context = get_channel_context

    async def execute(self, **kwargs: Any) -> ToolResult:
        person_id = str(kwargs.get("person_id") or "").strip()
        face_ref = str(kwargs.get("face_ref") or "").strip()
        if not person_id or not face_ref:
            return ToolResult(
                tool_name=self.name,
                output="Both 'person_id' and 'face_ref' are required.",
                success=False,
                error="missing required parameters",
                retryable=False,
            )

        ctx = _resolver_chat_context(self._get_channel_context)
        if ctx is None:
            return ToolResult(
                tool_name=self.name,
                output="add_photo_to_person only works inside an active conversation.",
                success=False,
                error="no channel context",
                retryable=False,
            )

        resolved = await _resolver_embedding_de_face_ref(
            face_ref,
            agent_id=self._agent_id,
            channel=ctx.channel_type,
            chat_id=ctx.user_id,
            metadata_repo=self._metadata_repo,
        )
        if resolved is None:
            return ToolResult(
                tool_name=self.name,
                output=f"face_ref '{face_ref}' not found in recent thread metadata.",
                success=False,
                error="face_ref not found",
                retryable=False,
            )

        embedding, source_history_id = resolved

        try:
            await self._registry.add_embedding_to_person(
                person_id=person_id,
                embedding=embedding,
                source_history_id=source_history_id,
                source_face_ref=face_ref,
            )
        except Exception as exc:
            logger.exception("AddPhotoToPersonTool: error con persona %s", person_id)
            return ToolResult(
                tool_name=self.name,
                output=f"Error adding embedding: {exc}",
                success=False,
                error=str(exc),
                retryable=True,
            )

        return ToolResult(
            tool_name=self.name,
            output=f"Embedding added to person {person_id}.",
            success=True,
        )


# ----------------------------------------------------------------------
# 3. UpdatePersonMetadataTool
# ----------------------------------------------------------------------


class UpdatePersonMetadataTool(ITool):
    name = "update_person_metadata"
    description = (
        "Update metadata fields of an existing person. "
        "Required: 'person_id'. "
        "Optional fields to update: 'nombre', 'apellido', 'fecha_nacimiento' "
        "(ISO YYYY-MM-DD), 'relacion', 'notes'. "
        "Only fields you specify are changed; others are preserved."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "person_id": {"type": "string"},
            "nombre": {"type": "string"},
            "apellido": {"type": "string"},
            "fecha_nacimiento": {"type": "string"},
            "relacion": {"type": "string"},
            "notes": {"type": "string"},
        },
        "required": ["person_id"],
    }

    def __init__(self, face_registry: IFaceRegistryPort) -> None:
        self._registry = face_registry

    async def execute(self, **kwargs: Any) -> ToolResult:
        person_id = str(kwargs.get("person_id") or "").strip()
        if not person_id:
            return ToolResult(
                tool_name=self.name,
                output="'person_id' is required.",
                success=False,
                error="missing person_id",
                retryable=False,
            )

        campos = {
            k: v
            for k, v in kwargs.items()
            if k != "person_id" and v is not None
        }
        if not campos:
            return ToolResult(
                tool_name=self.name,
                output="No fields to update.",
                success=False,
                error="no fields",
                retryable=False,
            )

        try:
            persona = await self._registry.update_person_metadata(person_id, **campos)
        except Exception as exc:
            logger.exception("UpdatePersonMetadataTool: error con %s", person_id)
            return ToolResult(
                tool_name=self.name,
                output=f"Error updating person: {exc}",
                success=False,
                error=str(exc),
                retryable=True,
            )

        return ToolResult(
            tool_name=self.name,
            output=f"Updated person: {_formatear_persona_breve(persona)}.",
            success=True,
        )


# ----------------------------------------------------------------------
# 4. ListKnownPersonsTool
# ----------------------------------------------------------------------


class ListKnownPersonsTool(ITool):
    name = "list_known_persons"
    description = (
        "List all known persons in the face registry. "
        "Optional: 'incluir_ignoradas' (boolean, default false). "
        "By default ignored faces (registered via skip_face) are excluded "
        "to avoid noise. Pass true to include them for audit."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "incluir_ignoradas": {"type": "boolean", "default": False},
        },
        "required": [],
    }

    def __init__(self, face_registry: IFaceRegistryPort) -> None:
        self._registry = face_registry

    async def execute(self, **kwargs: Any) -> ToolResult:
        incluir = bool(kwargs.get("incluir_ignoradas", False))
        try:
            personas = await self._registry.list_persons(incluir_ignoradas=incluir)
        except Exception as exc:
            logger.exception("ListKnownPersonsTool: error listando personas")
            return ToolResult(
                tool_name=self.name,
                output=f"Error listing persons: {exc}",
                success=False,
                error=str(exc),
                retryable=True,
            )

        if not personas:
            return ToolResult(
                tool_name=self.name,
                output="No known persons in the registry.",
                success=True,
            )

        lineas = [f"Found {len(personas)} known person(s):"]
        for p in personas:
            lineas.append(f"- {_formatear_persona_breve(p)}")
        return ToolResult(
            tool_name=self.name,
            output="\n".join(lineas),
            success=True,
        )


# ----------------------------------------------------------------------
# 5. ForgetPersonTool
# ----------------------------------------------------------------------


class ForgetPersonTool(ITool):
    name = "forget_person"
    description = (
        "Permanently delete a person and all their face embeddings from the registry. "
        "Use this for privacy when the user asks to remove someone. "
        "Required: 'person_id'. This is IRREVERSIBLE."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "person_id": {"type": "string"},
        },
        "required": ["person_id"],
    }

    def __init__(self, face_registry: IFaceRegistryPort) -> None:
        self._registry = face_registry

    async def execute(self, **kwargs: Any) -> ToolResult:
        person_id = str(kwargs.get("person_id") or "").strip()
        if not person_id:
            return ToolResult(
                tool_name=self.name,
                output="'person_id' is required.",
                success=False,
                error="missing person_id",
                retryable=False,
            )

        try:
            await self._registry.forget_person(person_id)
        except Exception as exc:
            logger.exception("ForgetPersonTool: error borrando %s", person_id)
            return ToolResult(
                tool_name=self.name,
                output=f"Error forgetting person: {exc}",
                success=False,
                error=str(exc),
                retryable=True,
            )

        return ToolResult(
            tool_name=self.name,
            output=f"Person {person_id} forgotten (all embeddings deleted).",
            success=True,
        )


# ----------------------------------------------------------------------
# 6. SkipFaceTool
# ----------------------------------------------------------------------


class SkipFaceTool(ITool):
    name = "skip_face"
    description = (
        "Mark a detected face as 'ignored' so future photos containing the same "
        "face are silently filtered (statues, paintings, strangers, etc.). "
        "This persists in the face DB with categoria='ignorada'. "
        "Required: 'face_ref' (the reference token of the detected face). "
        "Use this when the user says 'no me importa', 'es un cuadro', or similar."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "face_ref": {"type": "string"},
        },
        "required": ["face_ref"],
    }

    def __init__(
        self,
        face_registry: IFaceRegistryPort,
        metadata_repo: IMessageFaceMetadataRepo,
        agent_id: str,
        get_channel_context: Callable[[], ChannelContext | None],
    ) -> None:
        self._registry = face_registry
        self._metadata_repo = metadata_repo
        self._agent_id = agent_id
        self._get_channel_context = get_channel_context

    async def execute(self, **kwargs: Any) -> ToolResult:
        face_ref = str(kwargs.get("face_ref") or "").strip()
        if not face_ref:
            return ToolResult(
                tool_name=self.name,
                output="'face_ref' is required.",
                success=False,
                error="missing face_ref",
                retryable=False,
            )

        ctx = _resolver_chat_context(self._get_channel_context)
        if ctx is None:
            return ToolResult(
                tool_name=self.name,
                output="skip_face only works inside an active conversation.",
                success=False,
                error="no channel context",
                retryable=False,
            )

        resolved = await _resolver_embedding_de_face_ref(
            face_ref,
            agent_id=self._agent_id,
            channel=ctx.channel_type,
            chat_id=ctx.user_id,
            metadata_repo=self._metadata_repo,
        )
        if resolved is None:
            return ToolResult(
                tool_name=self.name,
                output=f"face_ref '{face_ref}' not found in recent thread metadata.",
                success=False,
                error="face_ref not found",
                retryable=False,
            )

        embedding, source_history_id = resolved

        try:
            persona = await self._registry.register_person(
                nombre=None,
                apellido=None,
                fecha_nacimiento=None,
                relacion=None,
                embedding=embedding,
                source_history_id=source_history_id,
                source_face_ref=face_ref,
                categoria="ignorada",
            )
        except Exception as exc:
            logger.exception("SkipFaceTool: error marcando %s como ignorada", face_ref)
            return ToolResult(
                tool_name=self.name,
                output=f"Error skipping face: {exc}",
                success=False,
                error=str(exc),
                retryable=True,
            )

        return ToolResult(
            tool_name=self.name,
            output=f"Face {face_ref} marked as ignored (id={persona.id}).",
            success=True,
        )


# ----------------------------------------------------------------------
# 7. MergePersonsTool
# ----------------------------------------------------------------------


class MergePersonsTool(ITool):
    name = "merge_persons"
    description = (
        "Merge two persons in the face registry. All embeddings from "
        "'source_id' are absorbed by 'target_id', and 'source_id' is deleted. "
        "Use this when the user confirms two records are the same person, or "
        "when the nightly dedup job proposes a merge."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "source_id": {"type": "string", "description": "Person to be deleted."},
            "target_id": {"type": "string", "description": "Person to absorb the embeddings."},
        },
        "required": ["source_id", "target_id"],
    }

    def __init__(self, face_registry: IFaceRegistryPort) -> None:
        self._registry = face_registry

    async def execute(self, **kwargs: Any) -> ToolResult:
        source_id = str(kwargs.get("source_id") or "").strip()
        target_id = str(kwargs.get("target_id") or "").strip()
        if not source_id or not target_id:
            return ToolResult(
                tool_name=self.name,
                output="Both 'source_id' and 'target_id' are required.",
                success=False,
                error="missing ids",
                retryable=False,
            )
        if source_id == target_id:
            return ToolResult(
                tool_name=self.name,
                output="source_id and target_id cannot be the same.",
                success=False,
                error="same id",
                retryable=False,
            )

        try:
            persona = await self._registry.merge_persons(source_id, target_id)
        except Exception as exc:
            logger.exception("MergePersonsTool: error fusionando %s→%s", source_id, target_id)
            return ToolResult(
                tool_name=self.name,
                output=f"Error merging persons: {exc}",
                success=False,
                error=str(exc),
                retryable=True,
            )

        return ToolResult(
            tool_name=self.name,
            output=(
                f"Merged. Resulting person: {_formatear_persona_breve(persona)} "
                f"with {persona.embeddings_count} total embeddings."
            ),
            success=True,
        )


# ----------------------------------------------------------------------
# 8. FindDuplicatePersonsTool (para el job nocturno)
# ----------------------------------------------------------------------


class FindDuplicatePersonsTool(ITool):
    name = "find_duplicate_persons"
    description = (
        "Scan all known persons (excluding ignored) and report pairs whose "
        "centroid embeddings are highly similar — candidates for merging. "
        "This is intended to be called by the nightly dedup cron job. "
        "Optional: 'similarity_threshold' (default 0.70). "
        "Returns pairs (id_a, id_b, similarity)."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "similarity_threshold": {
                "type": "number",
                "description": "Min cosine similarity to report a pair (default 0.70).",
            },
        },
        "required": [],
    }

    def __init__(
        self,
        face_registry: IFaceRegistryPort,
        default_threshold: float = 0.70,
    ) -> None:
        self._registry = face_registry
        self._default_threshold = default_threshold

    async def execute(self, **kwargs: Any) -> ToolResult:
        threshold = float(
            kwargs.get("similarity_threshold", self._default_threshold)
        )

        try:
            personas = await self._registry.list_persons(incluir_ignoradas=False)
        except Exception as exc:
            logger.exception("FindDuplicatePersonsTool: error listando personas")
            return ToolResult(
                tool_name=self.name,
                output=f"Error listing persons: {exc}",
                success=False,
                error=str(exc),
                retryable=True,
            )

        if len(personas) < 2:
            return ToolResult(
                tool_name=self.name,
                output="Less than 2 persons in registry — nothing to dedup.",
                success=True,
            )

        # Recolectar centroides
        centroides: dict[str, np.ndarray] = {}
        for p in personas:
            try:
                centroide = await self._registry.get_centroid(p.id)
            except Exception:  # noqa: BLE001
                centroide = None
            if centroide is not None:
                centroides[p.id] = centroide

        # Pairwise cosine similarity
        ids = list(centroides.keys())
        candidatos: list[tuple[str, str, float]] = []
        for i, id_a in enumerate(ids):
            for id_b in ids[i + 1 :]:
                vec_a = centroides[id_a]
                vec_b = centroides[id_b]
                norma = float(np.linalg.norm(vec_a) * np.linalg.norm(vec_b))
                if norma == 0:
                    continue
                sim = float(np.dot(vec_a, vec_b) / norma)
                if sim >= threshold:
                    candidatos.append((id_a, id_b, sim))

        if not candidatos:
            return ToolResult(
                tool_name=self.name,
                output=f"No duplicate candidates found (threshold={threshold:.2f}).",
                success=True,
            )

        candidatos.sort(key=lambda t: t[2], reverse=True)
        lineas = [f"Found {len(candidatos)} duplicate candidate(s) (threshold={threshold:.2f}):"]
        for id_a, id_b, sim in candidatos:
            lineas.append(f"- {id_a} ↔ {id_b} (similitud {sim:.3f})")
        return ToolResult(
            tool_name=self.name,
            output="\n".join(lineas),
            success=True,
        )
