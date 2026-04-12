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


class SchedulerError(IñakiError):
    """Base para errores del scheduler."""


class BuiltinTaskProtectedError(SchedulerError):
    """Intento de modificar o eliminar una tarea builtin protegida."""


class InvalidTriggerTypeError(SchedulerError):
    """Tipo de trigger no soportado."""


class TaskNotFoundError(SchedulerError):
    """La tarea solicitada no existe."""


class TooManyActiveTasksError(SchedulerError):
    """El agente alcanzó el máximo de tareas activas permitidas."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"Agent {agent_id} has reached the maximum of 21 active tasks")
        self.agent_id = agent_id


class ToolLoopMaxIterationsError(IñakiError):
    """El tool-loop alcanzó el límite de iteraciones sin completar la tarea."""

    def __init__(self, last_response: str) -> None:
        super().__init__(f"Max iterations reached. Last response: {last_response!r}")
        self.last_response = last_response
