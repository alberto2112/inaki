"""Bloque ``chat_history``: memoria a corto plazo (ventana de conversación).

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

from pydantic import ConfigDict

from inaki.config.schema._base import RuntimePath, _ConfigBaseModel


class ChatHistoryConfig(_ConfigBaseModel):
    """Memoria a CORTO plazo: qué conversación previa ve el LLM en cada turno.

    Bloque per-agente. Es la ventana deslizante que se inyecta al prompt, distinta
    de ``memories`` (memoria a largo plazo, destilada por el job nocturno).

    Los dos knobs de acá gobiernan el costo de contexto de cada turno: cuánto
    historial entra (``max_messages``) y cuánto rastro de tools se guarda
    (``persist_tool_calls`` / ``persist_tool_result_max_chars``).
    """

    model_config = ConfigDict(validate_default=True)  # RuntimePath en los defaults

    db_filename: RuntimePath = "data/history.db"
    """Fichero SQLite del historial de conversación.

    Relativo al home de instancia; se reancla con ``--home`` / ``INAKI_HOME``. Un
    path absoluto se usa tal cual. Los agentes que comparten fichero se aíslan por
    ``agent_id``; para aislamiento FÍSICO, dale a cada agente su propio
    ``db_filename``."""

    max_messages: int = 0
    """Últimos N mensajes del scope que se inyectan al LLM. ``0`` = sin límite.

    Es una ventana deslizante sobre la lectura, no una política de borrado: los
    mensajes viejos siguen en la DB. Bajarlo es la palanca directa para recortar
    tokens de prompt contra modelos con ventana chica. OJO: el conteo dentro de
    una ventana llena no sirve para detectar mensajes nuevos — el drain in-flight
    cursa por rowid monotónico justamente por eso."""

    merge_chats: bool = False
    """Política de aislamiento del historial. ``False`` = un hilo por ``(channel, chat_id)``.

    Con ``False`` (default) el agente solo ve los mensajes de la conversación
    actual: privado de Telegram, grupo y CLI quedan separados. Con ``True``
    comparte un único historial entre todos sus canales y chats — útil para que
    lo hablado en privado esté disponible al responder en grupo, a costa de
    filtrar contexto entre conversaciones."""

    persist_tool_calls: bool = True
    """Persistir el par assistant+tool_calls ↔ tool_results en el historial.

    Default ``True``: el agente principal tiene memoria episódica de sus propias
    acciones entre turnos (no olvida en qué path escribió con ``write_file``, ni
    qué ficheros ya mandó). Con ``False`` el rastro vive solo en el tool loop del
    turno y se descarta — el agente queda amnésico de su actividad con
    herramientas, que es lo que producía el patrón "afirmo haber hecho algo que
    no recuerdo haber hecho". Solo afecta al agente principal; los subagentes
    one-shot quedan afuera por diseño. Ver las notas de migración
    ``persist-tool-calls`` y ``outbound-send-single-owner`` en ``CLAUDE.md``."""

    persist_tool_result_max_chars: int = 2000
    """Truncación (en chars) de cada tool result al persistirlo con
    ``persist_tool_calls``. Acota el costo de contexto y disco cuando una tool
    devuelve un volcado grande (web_search, RAG). ``0`` = sin truncar. El turno
    en curso siempre ve el result completo; solo la copia persistida se recorta."""
