"""Inyección de timestamps al ``content`` de mensajes USER/ASSISTANT.

El servicio devuelve una nueva lista de mensajes con prefijo ``[ts] `` en el
``content``. Pensado para llamarse desde el use case ``RunAgentUseCase`` justo
antes de invocar el tool loop, cuando el canal lo solicite.
"""

from __future__ import annotations

from core.domain.entities.message import Message, Role

_FORMATO_TS = "%Y-%m-%d %H:%M:%S %Z"


def prepend_timestamps(messages: list[Message]) -> list[Message]:
    """Devuelve una nueva lista con prefijo ``[YYYY-MM-DD HH:MM:SS TZ] `` en el content.

    Reglas:

    - Solo aplica a roles ``USER`` y ``ASSISTANT``. ``TOOL``, ``TOOL_RESULT`` y
      ``SYSTEM`` se devuelven intactos: el timestamp en esos roles es ruido para
      el LLM.
    - Mensajes con ``timestamp is None`` quedan intactos. Es el caso de los
      working messages efímeros del tool loop (assistant intermedios, tool
      results) que aún no fueron persistidos.
    - Mensajes con ``content`` vacío quedan intactos. Evita romper el contrato
      OpenAI ``content=None`` que usa ``BaseLLMProvider._build_messages`` para
      assistants que solo cargan ``tool_calls``.
    - La función NO muta los mensajes originales: usa ``model_copy`` para
      construir copias cuando hay que prefijar.
    - El timestamp se formatea en zona local del sistema vía
      ``datetime.astimezone()`` (sin argumento), consistente con el resto del
      proyecto.
    """
    out: list[Message] = []
    for m in messages:
        if m.role in (Role.USER, Role.ASSISTANT) and m.timestamp is not None and m.content:
            ts = m.timestamp.astimezone().strftime(_FORMATO_TS)
            out.append(m.model_copy(update={"content": f"[{ts}] {m.content}"}))
        else:
            out.append(m)
    return out
