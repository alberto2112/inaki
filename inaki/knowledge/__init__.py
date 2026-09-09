"""Knowledge (RAG sobre documentos y fuentes SQLite) — harness-global.

``orchestrator`` implementa ``IKnowledgeRetriever`` (el port del kernel) sobre N
``IKnowledgeSource``; ``adapters`` son las fuentes; ``use_cases.manage_knowledge`` la
gestión (ingest, list, delete) que exponen la tool ``knowledge_admin`` y el CLI.
"""
