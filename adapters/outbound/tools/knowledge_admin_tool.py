"""KnowledgeAdminTool — gestión del knowledge base desde el LLM.

Una sola tool con discriminador ``operation`` (ingest/reindex/list/stats/delete/sources)
sobre ``ManageKnowledgeUseCase``. Es el camino por el que cualquier canal aporta
documentos al RAG: Telegram ya entrega el path del archivo descargado al LLM, el
LLM llama ``operation=ingest`` y el documento queda indexado — sin código de
knowledge en el adapter del canal.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.domain.errors import KnowledgeError
from core.ports.outbound.tool_port import ITool, ToolResult
from core.use_cases.manage_knowledge import ManageKnowledgeUseCase

logger = logging.getLogger(__name__)


class KnowledgeAdminTool(ITool):
    name = "knowledge_admin"
    description = (
        "Manage the agent's document knowledge base (RAG). "
        "Use it to add a document so its content becomes searchable, or to inspect "
        "and clean up the index. "
        "Required parameter: 'operation', one of: "
        "'ingest' (add+index a file; needs 'path'), "
        "'reindex' (re-scan a source), "
        "'list' (show indexed files), "
        "'stats' (index statistics), "
        "'delete' (remove a file from the index; needs 'file_path'), "
        "'sources' (list manageable sources). "
        "Optional 'source' selects which knowledge source to act on (omit it when "
        "there is only one). For 'ingest', pass the local 'path' of the file — when "
        "a user sends a document through a channel, that path is already provided to you. "
        "For 'delete', pass 'file_path' (filename or full path as shown by 'list'); "
        "set 'remove_file' to also delete the physical file."
    )
    # Disparadores multilingües SOLO para el embedding del semantic routing
    # (no van al schema del LLM). Cómo un humano pide gestionar el knowledge.
    routing_keywords = (
        "guardá este documento, agregá esto al conocimiento, indexá este archivo, "
        "metelo en la base de conocimiento, sumá este texto al RAG, "
        "qué documentos tenés indexados, listá el conocimiento, borrá ese documento, "
        "eliminá ese archivo del conocimiento, reindexá la base, estadísticas del índice. "
        "save this document, add this to the knowledge base, index this file, "
        "ingest this into the RAG, what documents do you have indexed, "
        "list the knowledge base, delete that document, remove that file from knowledge, "
        "reindex the knowledge, knowledge index stats. "
        "enregistre ce document, ajoute ceci à la base de connaissances, indexe ce fichier, "
        "quels documents as-tu indexés, supprime ce document, réindexe la base."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["ingest", "reindex", "list", "stats", "delete", "sources"],
                "description": "The management action to perform.",
            },
            "source": {
                "type": "string",
                "description": (
                    "Knowledge source ID to act on. Omit when there is a single "
                    "indexable source. Use 'sources' to discover available IDs."
                ),
            },
            "path": {
                "type": "string",
                "description": "Local path of the file to ingest (required for 'ingest').",
            },
            "file_path": {
                "type": "string",
                "description": (
                    "File to remove from the index (required for 'delete'). "
                    "Filename or full path as shown by 'list'."
                ),
            },
            "remove_file": {
                "type": "boolean",
                "description": (
                    "With 'delete': also delete the physical file (only if it lives "
                    "inside the source folder). Default false (index-only)."
                ),
            },
        },
        "required": ["operation"],
    }

    def __init__(self, manage_knowledge: ManageKnowledgeUseCase) -> None:
        self._uc = manage_knowledge

    def _fail(self, mensaje: str, *, retryable: bool = False) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            output=mensaje,
            success=False,
            error=mensaje,
            retryable=retryable,
        )

    async def execute(self, **kwargs) -> ToolResult:
        operation = str(kwargs.get("operation") or "").strip().lower()
        source = kwargs.get("source") or None

        if not operation:
            return self._fail("The 'operation' parameter is required.")

        try:
            if operation == "ingest":
                path = str(kwargs.get("path") or "").strip()
                if not path:
                    return self._fail("'path' is required for operation 'ingest'.")
                result = await self._uc.ingest(Path(path), source_id=source)
                return ToolResult(
                    tool_name=self.name,
                    output=(
                        f"Ingested into '{result['source_id']}': "
                        f"{result.get('chunks_nuevos', 0)} chunk(s) indexed "
                        f"(stored at {result.get('stored_path', '?')})."
                    ),
                    success=True,
                )

            if operation == "reindex":
                stats = await self._uc.reindex(source_id=source)
                return ToolResult(
                    tool_name=self.name,
                    output=(
                        f"Reindexed '{stats['source_id']}': "
                        f"processed={stats.get('archivos_procesados', 0)}, "
                        f"skipped={stats.get('archivos_saltados', 0)}, "
                        f"new chunks={stats.get('chunks_nuevos', 0)}."
                    ),
                    success=True,
                )

            if operation == "list":
                files = await self._uc.list_documents(source_id=source)
                if not files:
                    return ToolResult(
                        tool_name=self.name, output="No documents indexed.", success=True
                    )
                lines = [f"{len(files)} document(s) indexed:"]
                for f in files:
                    lines.append(f"- {f['file_path']} ({f['chunk_count']} chunks)")
                return ToolResult(tool_name=self.name, output="\n".join(lines), success=True)

            if operation == "stats":
                info = await self._uc.stats(source_id=source)
                lines = [
                    f"Source:        {info['source_id']}",
                    f"Files indexed: {info['archivos_indexados']}",
                    f"Total chunks:  {info['chunks_totales']}",
                    f"Embedding dim: {info['embedding_dimension']}",
                ]
                return ToolResult(tool_name=self.name, output="\n".join(lines), success=True)

            if operation == "delete":
                file_path = str(kwargs.get("file_path") or "").strip()
                if not file_path:
                    return self._fail("'file_path' is required for operation 'delete'.")
                result = await self._uc.delete_document(
                    file_path,
                    source_id=source,
                    remove_physical=bool(kwargs.get("remove_file")),
                )
                return ToolResult(
                    tool_name=self.name,
                    output=(
                        f"Deleted {result['chunks_borrados']} chunk(s) of "
                        f"'{result['file_path']}' from '{result['source_id']}'."
                    ),
                    success=True,
                )

            if operation == "sources":
                fuentes = self._uc.list_sources()
                if not fuentes:
                    return ToolResult(
                        tool_name=self.name,
                        output="No indexable knowledge sources configured.",
                        success=True,
                    )
                lines = ["Indexable sources:"]
                for s in fuentes:
                    lines.append(f"- {s['source_id']}: {s['description']}")
                return ToolResult(tool_name=self.name, output="\n".join(lines), success=True)

            return self._fail(f"Unknown operation '{operation}'.")

        except FileNotFoundError as exc:
            return self._fail(f"File not found: {exc}")
        except KnowledgeError as exc:
            return self._fail(str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("KnowledgeAdminTool: error en operation=%s", operation)
            return ToolResult(
                tool_name=self.name,
                output=f"Error managing knowledge: {exc}",
                success=False,
                error=str(exc),
                retryable=True,
            )
