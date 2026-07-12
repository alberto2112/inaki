from abc import ABC, abstractmethod
from core.domain.entities.message import Message
from core.domain.value_objects.conversation_state import ConversationState


class IHistoryStore(ABC):
    @abstractmethod
    async def append(
        self,
        agent_id: str,
        message: Message,
        channel: str = "",
        chat_id: str = "",
    ) -> int | None:
        """
        Persiste un mensaje en el historial del agente.

        Args:
            agent_id: Identificador del agente propietario del historial.
            message: Mensaje a persistir. Se aceptan ``Role.USER``, ``Role.ASSISTANT``
                y ``Role.TOOL`` (este último para el rastro de tool calls del feature
                persist-tool-calls); cualquier otro rol se ignora y retorna ``None``.
            channel: Canal de origen del mensaje (ej: ``"telegram"``, ``"cli"``).
                     Cadena vacía cuando el canal no aplica o no es relevante.
            chat_id: Identificador del chat dentro del canal (ej: ID de grupo Telegram).
                     Cadena vacía para chats privados o canales sin distinción de chat.

        Returns:
            El ID autoincremental de la fila insertada, o ``None`` si el rol no
            se persiste (p. ej. tool_call). Útil para obtener el ``history_id``
            necesario al vincular metadata de fotos.
        """
        ...

    @abstractmethod
    async def update_content(
        self,
        agent_id: str,
        message_id: int,
        new_content: str,
    ) -> bool:
        """Reemplaza el ``content`` de un mensaje existente del historial.

        Pensado para flujos donde un mensaje se persiste primero como placeholder
        (p. ej. el bloque ``@photo`` con un ``history_id`` reservado para asociar
        metadata de caras) y luego se enriquece con el contenido real una vez que el
        procesamiento asíncrono termina. Mantiene el ``id``, el ``created_at`` y
        el orden cronológico intactos — solo cambia el texto.

        Args:
            agent_id: Identificador del agente propietario (defensa en profundidad
                para evitar updates cross-agente).
            message_id: ID autoincremental del row a actualizar.
            new_content: Nuevo contenido a setear.

        Returns:
            ``True`` si se actualizó alguna fila, ``False`` si no se encontró el
            mensaje (por agent_id mismatch o id inexistente).
        """
        ...

    @abstractmethod
    async def load(
        self,
        agent_id: str,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[Message]:
        """
        Retorna los mensajes del historial (ventana en memoria si está configurada).

        Args:
            agent_id: Identificador del agente.
            channel: Si no es ``None``, filtra por canal exacto.
            chat_id: Si no es ``None``, filtra por chat_id exacto.
        """
        ...

    @abstractmethod
    async def load_full(self, agent_id: str) -> list[Message]:
        """Retorna el historial completo activo."""
        ...

    @abstractmethod
    async def last_row_id(
        self,
        agent_id: str,
        channel: str = "",
        chat_id: str = "",
    ) -> int:
        """ID de la última fila persistida en el scope (0 si no hay ninguna).

        Baseline del cursor de drainage (in-flight-message-injection): el tool
        loop drena las filas ``role=user`` con id MAYOR a este valor. A
        diferencia de contar mensajes sobre ``load()`` (que aplica la ventana
        ``max_messages`` — el conteo dentro de una ventana deslizante LLENA no
        crece cuando entra un mensaje nuevo y expulsa otro), el rowid es
        monotónico e inmune a la ventana.
        """
        ...

    @abstractmethod
    async def load_user_messages_since(
        self,
        agent_id: str,
        after_id: int,
        channel: str = "",
        chat_id: str = "",
    ) -> tuple[int, list[Message]]:
        """Mensajes ``role=user`` del scope con id > ``after_id``, en orden.

        Primitiva del drainage in-flight: devuelve ``(nuevo_cursor, mensajes)``
        donde ``nuevo_cursor`` es el id de la última fila devuelta — o
        ``after_id`` intacto si no hay filas nuevas. Consulta por rowid, NUNCA
        sobre la ventana ``max_messages`` (ver ``last_row_id``).
        """
        ...

    @abstractmethod
    async def search(
        self,
        agent_id: str,
        query: str | None = None,
        role: str | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        limit: int = 20,
    ) -> list[Message]:
        """Busca mensajes crudos del historial del agente por texto, rol y/o scope.

        Pensado para consultas puntuales ("¿qué se dijo sobre X?", "revisá la
        conversación con el chat Y") que devuelven los mensajes TAL CUAL, sin
        cargar toda la ventana en memoria ni pasar por el extractor de recuerdos.

        Siempre filtra por ``agent_id`` (aislamiento cross-agente, igual que el
        resto del port — un agente nunca ve el historial de otro).

        Args:
            agent_id: Identificador del agente. Filtro obligatorio.
            query: Subcadena a buscar en ``content`` (``LIKE %query%``,
                case-insensitive). ``None`` o vacío → sin filtro de texto.
            role: Si no es ``None``, filtra por rol exacto (``"user"`` / ``"assistant"``).
            channel: Si no es ``None``, filtra por canal exacto.
            chat_id: Si no es ``None``, apunta a UNA conversación. ``None`` →
                busca en todo el historial del agente.
            limit: Máximo de mensajes a devolver.

        Returns:
            Lista de ``Message`` (cada uno con su ``channel``/``chat_id`` de
            origen), ordenada de más reciente a más antiguo.
        """
        ...

    @abstractmethod
    async def load_uninfused(
        self,
        agent_id: str,
        channels: list[str] | None = None,
    ) -> list[Message]:
        """
        Retorna los mensajes que aún no han pasado por el extractor de recuerdos
        (flag ``infused=0``). Usado por la consolidación para evitar re-extraer
        hechos de mensajes ya procesados que siguen vivos en el buffer por el
        trim (keep_last).

        Args:
            agent_id: Identificador del agente.
            channels: Si es una lista no vacía, solo retorna mensajes cuyos
                ``channel`` estén en esa lista. ``None`` o lista vacía → sin filtro.
        """
        ...

    @abstractmethod
    async def mark_infused(
        self,
        agent_id: str,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> int:
        """
        Marca como ``infused=1`` los mensajes del scope ``(agent_id, channel, chat_id)``.

        ``channel`` y ``chat_id`` identifican el scope EXACTO:
        - ``None`` significa que ese campo es ``NULL`` en la fila (recuerdos
          pre-migración), NO "sin filtro". Se usa ``IS NULL`` en el SQL.
        - Un valor no-``None`` filtra con ``= ?`` exacto.

        Se llama dentro del loop de consolidación, tras procesar exitosamente
        cada scope, para que los scopes anteriores queden marcados incluso si
        un scope posterior falla. Retorna el número de filas afectadas.
        """
        ...

    @abstractmethod
    async def trim(self, agent_id: str, keep_last: int) -> None:
        """
        Borra todos los mensajes del agente salvo los N más recientes
        POR SCOPE ``(channel, chat_id)``.

        Se llama tras una consolidación exitosa: los recuerdos relevantes ya
        están extraídos al storage vectorial, pero preservamos los últimos N
        mensajes de CADA conversación como contexto inmediato para el próximo turno.

        Si `keep_last <= 0` no borra nada (no-op defensivo).
        Si un scope tiene menos mensajes que `keep_last`, no borra nada de ese scope.
        """
        ...

    @abstractmethod
    async def clear(
        self,
        agent_id: str,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        """Elimina historial del agente.

        Si tanto ``channel`` como ``chat_id`` son ``None`` borra TODO el historial
        del agente y también el ``agent_state`` (sticky skills/tools), manteniendo
        ambos en sincronía. Es el modo "limpieza total" (``/clear_all`` en Telegram,
        ``DELETE /history`` en REST, ``/clear`` en CLI).

        Si se proveen ``channel`` y/o ``chat_id`` borra los mensajes que matchean
        ese filtro Y el ``agent_state`` del mismo scope. Es el modo "limpieza
        scoped" (``/clear`` en Telegram).
        """
        ...

    @abstractmethod
    async def load_state(
        self,
        agent_id: str,
        channel: str = "",
        chat_id: str = "",
    ) -> ConversationState:
        """Retorna el estado conversacional persistido para el scope ``(agent_id, channel, chat_id)``.

        Si no existe estado previo (primer turno o tras ``clear``), devuelve un
        ``ConversationState`` vacío. Nunca retorna ``None``.
        """
        ...

    @abstractmethod
    async def save_state(
        self,
        agent_id: str,
        state: ConversationState,
        channel: str = "",
        chat_id: str = "",
    ) -> None:
        """Persiste el estado conversacional del scope ``(agent_id, channel, chat_id)`` (upsert).

        Actualiza ``updated_at`` con la marca de tiempo UTC del momento del save.
        """
        ...
