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
    """El agente alcanzó el límite máximo de tareas activas."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"Agent {agent_id} has reached the maximum of 21 active tasks")
        self.agent_id = agent_id


class ToolLoopMaxIterationsError(IñakiError):
    """El tool-loop alcanzó el límite de iteraciones sin completar la tarea."""

    def __init__(self, last_response: str) -> None:
        super().__init__(f"Max iterations reached. Last response: {last_response!r}")
        self.last_response = last_response


# ---------------------------------------------------------------------------
# Daemon client
# ---------------------------------------------------------------------------


class DaemonError(IñakiError):
    """Base para errores de comunicación con el daemon."""


class DaemonNotRunningError(DaemonError):
    """El daemon no está corriendo o no es alcanzable."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "El daemon no está corriendo. Iniciá con `inaki daemon` o `systemctl start inaki`."
        )


class DaemonTimeoutError(DaemonError):
    """Timeout al comunicarse con el daemon."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message or "Timeout esperando respuesta del daemon. Verificá que esté funcionando."
        )


class DaemonClientError(DaemonError):
    """Error HTTP del daemon (status code != 2xx)."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"Error del daemon (HTTP {status_code}): {detail}")
        self.status_code = status_code
        self.detail = detail


class UnknownAgentError(DaemonClientError):
    """El agent_id solicitado no existe en el daemon (HTTP 404)."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(
            status_code=404,
            detail=f"Agente '{agent_id}' no existe en el daemon.",
        )
        self.agent_id = agent_id


class DaemonAuthError(DaemonClientError):
    """Autenticación rechazada por el daemon (HTTP 401/403)."""

    def __init__(self, status_code: int = 401) -> None:
        super().__init__(
            status_code=status_code,
            detail="Auth inválida. Verificá la X-Admin-Key en ~/.inaki/config/global.secrets.yaml.",
        )
