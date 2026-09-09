"""Memoria del agente: episódica (historial), semántica (memorias con embeddings), consolidación y reconciliación.

Adapters SQLite, use cases de consolidación/reconciliación y las tools de memoria e
historial. Los ports (``IHistoryStore``, ``IMemoryRepository``) y ``MemorySettings`` los posee
el kernel, que es quien los consume en cada turno.
"""
