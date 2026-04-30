class IñakiError(Exception):
    """Base exception para todos los errores del dominio."""


class AgentNotFoundError(IñakiError):
    """El agente solicitado no existe en el registry."""


class LLMError(IñakiError):
    """Error al llamar al proveedor LLM."""


class ConfigError(IñakiError):
    """Error de configuración detectado al cargar o resolver config."""


class ConsolidationError(IñakiError):
    """Error durante la consolidación de memoria."""


class EmbeddingError(IñakiError):
    """Error al generar embeddings."""


class TranscriptionError(IñakiError):
    """Error al transcribir audio (provider remoto, timeout, formato, etc.)."""


class TranscriptionFileTooLargeError(TranscriptionError):
    """El audio supera el límite del provider de transcripción."""

    def __init__(self, size_bytes: int, limit_bytes: int) -> None:
        super().__init__(f"Audio demasiado grande: {size_bytes} bytes > límite {limit_bytes} bytes")
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes


class UnknownTranscriptionProviderError(TranscriptionError):
    """El provider de transcripción solicitado no está registrado en la factory."""


class KnowledgeConfigError(IñakiError):
    """Error de configuración de una fuente de conocimiento.

    Se lanza al validar la DB de usuario: dimensión de embeddings incorrecta,
    tablas requeridas ausentes, o path inaccesible.
    """


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
# Vision / Reconocimiento facial
# ---------------------------------------------------------------------------


class VisionError(IñakiError):
    """Error al procesar una imagen con el proveedor de visión (InsightFace, etc.)."""


class SceneDescriptionError(IñakiError):
    """Error al describir la escena de una imagen con el proveedor LLM multimodal."""


class FaceRegistryError(IñakiError):
    """Error al leer o escribir el registro de personas en faces.db."""


class EmbeddingDimensionMismatchError(FaceRegistryError):
    """El modelo configurado produce embeddings de dimensión distinta a la registrada en faces.db.

    Al arrancar, el adaptador compara la dimensión del modelo cargado contra
    el valor de `schema_meta.embedding_dim` en faces.db. Si no coinciden,
    se lanza este error con un mensaje claro indicando la acción requerida:
    borrar faces.db y re-enrolar todas las personas.
    """

    def __init__(self, esperada: int, encontrada: int, modelo: str) -> None:
        super().__init__(
            f"Dimensión de embedding incompatible: la base de datos faces.db espera "
            f"{esperada} dimensiones (modelo '{modelo}' del schema guardado), "
            f"pero el modelo configurado produce {encontrada}. "
            f"Para resolver: detener el daemon, borrar ~/.inaki/data/faces.db y "
            f"reiniciar. Se perderán todas las caras registradas — volver a enrolar."
        )
        self.esperada = esperada
        self.encontrada = encontrada
        self.modelo = modelo


class UnknownVisionProviderError(VisionError):
    """El proveedor de visión solicitado no está registrado en la factory."""

    def __init__(self, provider: str, disponibles: list[str]) -> None:
        super().__init__(
            f"Proveedor de visión desconocido: '{provider}'. "
            f"Disponibles: {disponibles}"
        )
        self.provider = provider
        self.disponibles = disponibles


class UnknownSceneProviderError(SceneDescriptionError):
    """El proveedor de descripción de escena solicitado no está registrado en la factory."""

    def __init__(self, provider: str, disponibles: list[str]) -> None:
        super().__init__(
            f"Proveedor de descripción de escena desconocido: '{provider}'. "
            f"Disponibles: {disponibles}"
        )
        self.provider = provider
        self.disponibles = disponibles


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


# ---------------------------------------------------------------------------
# Config repository (setup TUI)
# ---------------------------------------------------------------------------


class AgentYaExisteError(IñakiError):
    """El id de agente ya está ocupado — no se puede crear un agente con ese id."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"El agente '{agent_id}' ya existe. Elegí un id distinto.")
        self.agent_id = agent_id


class ReferenciaInvalidaError(IñakiError):
    """Una referencia cruzada en la config apunta a un recurso inexistente."""

    def __init__(self, campo: str, valor: str, disponibles: list[str]) -> None:
        super().__init__(
            f"Referencia inválida: '{campo}' apunta a '{valor}', "
            f"que no existe. Disponibles: {disponibles}"
        )
        self.campo = campo
        self.valor = valor
        self.disponibles = disponibles
