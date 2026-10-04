"""Inyección de timestamps al ``content`` de mensajes USER.

El servicio devuelve una nueva lista de mensajes con prefijo ``[ts] `` en el
``content``. Pensado para llamarse desde el use case ``RunAgentUseCase`` justo
antes de invocar el tool loop, cuando el canal lo solicite.
"""

from __future__ import annotations

from inaki.shared.message import Message, Role

_FORMATO_TS = "%Y-%m-%d %H:%M:%S %Z"


def prepend_timestamps(messages: list[Message]) -> list[Message]:
    """Devuelve una nueva lista con prefijo ``[YYYY-MM-DD HH:MM:SS TZ] `` en el content.

    Reglas:

    - Solo aplica al rol ``USER``. ``ASSISTANT`` queda intacto a propósito: si
      el modelo ve sus propias respuestas empezando con ``[ts]``, aprende del
      contexto que así habla él y empieza a contestar con un timestamp delante.
      La línea de tiempo ya la dan los turnos del usuario. ``TOOL``,
      ``TOOL_RESULT`` y ``SYSTEM`` también quedan intactos: ahí es ruido.
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
        if m.role == Role.USER and m.timestamp is not None and m.content:
            ts = m.timestamp.astimezone().strftime(_FORMATO_TS)
            out.append(m.model_copy(update={"content": f"[{ts}] {m.content}"}))
        else:
            out.append(m)
    return out
