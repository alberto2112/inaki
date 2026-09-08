"""Primitivas de dominio compartidas por TODOS los módulos de Inaki.

Regla dura: este paquete no importa nada del proyecto (ni ``core``, ni
``adapters``, ni ``infrastructure``, ni otro subpaquete de ``inaki``). Solo
stdlib y ``pydantic``. La verifica ``lint-imports`` (``pyproject.toml``).

Qué vive acá y por qué:

- ``message``: ``Message`` y ``Role`` — la unidad de conversación que cruzan el
  kernel, los canales, la memoria y el scheduler.
- ``attachment``: la gramática ``@photo``/``@audio``/... con la que CUALQUIER
  canal persiste media (``attachment-grammar``).
- ``channel_context``: identidad del turno (canal, chat, remitente).
- ``errors``: la jerarquía de errores del proyecto. Los errores específicos de
  un módulo se mudan con él cuando ese módulo se extraiga.
- ``skip_marker``: el marcador ``__SKIP__`` y su detección tolerante.

Lo que NO va acá: ports (los posee quien los consume), use cases, adapters.
"""
