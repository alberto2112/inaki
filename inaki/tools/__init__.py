"""Tools del LLM: registro con routing semántico, builtins y Tool Config Protocol.

Los contratos que consume el turno (``ITool``, ``ToolResult``, ``IToolExecutor``,
``IToolConfigStore``) siguen en ``core/ports/outbound/`` hasta que el kernel se
mude a ``inaki/kernel``: es el contrato que las extensiones importan.
"""
