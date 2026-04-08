class IñakiError(Exception):
    """Base exception para todos los errores del dominio."""


class AgentNotFoundError(IñakiError):
    """El agente solicitado no existe en el registry."""


class LLMError(IñakiError):
    """Error al llamar al proveedor LLM."""


class ConsolidationError(IñakiError):
    """Error durante la consolidación de memoria."""


class EmbeddingError(IñakiError):
    """Error al generar embeddings."""


class ToolError(IñakiError):
    """Error al ejecutar una tool."""


class HistoryError(IñakiError):
    """Error al leer o escribir el historial."""
