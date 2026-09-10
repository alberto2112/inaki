"""El turno conversacional de Telegram: correr el agente y ENTREGAR la respuesta.

Dos caminos con el mismo ciclo (execute → ``__SKIP__`` → reply → egress de
broadcast → errores de red), que difieren solo en de dónde sale el turno:

- ``run``: hay un ``Update`` concreto detrás (privado, mention en grupo, voz o
  foto). Responde citando el mensaje (``reply_text``) y, en privados, aplica la
  inyección in-flight (``dispatch_inbound_turn``).
- ``run_group``: el flush del buffer de grupo. No hay ``Update``: la query se
  deriva del trailing batch del historial y se envía con ``send_message``.
"""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.error import BadRequest, NetworkError
from telegram.ext import Application

from inaki.channels.telegram.broadcast.egress import BroadcastEgress
from inaki.channels.telegram.broadcast.port import BroadcastReceiver
from inaki.channels.telegram.message_mapper import (
    _TIPOS_GRUPO,
    _safe_optional_str,
    compose_sender_identity,
    send_html_or_plain,
)
from inaki.channels.telegram.ports import TelegramBotPorts
from inaki.channels.telegram.reactions import Reactions
from inaki.kernel.ports.channel_port import IIntermediateSink, OutboundIntermediateSink
from inaki.kernel.turn_dispatch import dispatch_inbound_turn
from inaki.shared.channel_context import ChannelContext
from inaki.shared.skip_marker import SKIP_MARKER, is_skip_response

logger = logging.getLogger(__name__)

_AUTONOMOUS_SECTION = (
    "## Modo autónomo\n"
    "Si después de leer el contexto considerás que no tenés nada útil que aportar "
    "al grupo, respondé EXACTAMENTE con `__SKIP__` (mayúsculas, doble guion bajo "
    "antes y después, sin llamar ninguna tool, nada más). El sistema detecta ese "
    "marcador y no enviará nada al grupo."
)


class TurnRunner:
    def __init__(
        self,
        *,
        ports: TelegramBotPorts,
        app: Application,
        agent_id: str,
        behavior: str,
        broadcast_receiver: BroadcastReceiver | None,
        egress: BroadcastEgress,
        reactions: Reactions,
    ) -> None:
        self._ports = ports
        self._app = app
        self._agent_id = agent_id
        self._behavior = behavior
        self._receiver = broadcast_receiver
        self._egress = egress
        self._reactions = reactions

    @property
    def receiver(self) -> BroadcastReceiver | None:
        return self._receiver

    @receiver.setter
    def receiver(self, value: BroadcastReceiver | None) -> None:
        self._receiver = value

    async def run(
        self,
        update: Update,
        user_input: str | None,
        chat_type: str = "private",
        extra_sections: list[str] | None = None,
    ) -> None:
        """Ejecuta el agente con ``user_input`` (texto tipeado, transcripto o formateado de grupo).

        Args:
            update: Update de Telegram.
            user_input: Texto ya formateado para el LLM (con prefijo de usuario si es grupo).
                Si es ``None``, ``run_agent.execute`` deriva la query del trailing batch
                ``role=user`` del historial (modo history-derived). Usado por el handler
                de fotos cuando el placeholder ya fue enriquecido vía ``update_message_content``.
            chat_type: Tipo de chat (``"private"``, ``"group"``, ``"supergroup"``, ``"channel"``).
            extra_sections: Secciones adicionales del system prompt. Se pasan via
                ``set_extra_system_sections`` ANTES de invocar ``execute``.
        """
        chat = update.effective_chat
        user = update.effective_user
        message = update.message
        if chat is None or user is None or message is None:
            return
        chat_id = chat.id
        es_grupo = chat_type in _TIPOS_GRUPO
        secciones: list[str] = list(extra_sections or [])

        # Inyectar contexto de broadcast si hay receiver y es un grupo.
        if es_grupo and self._receiver is not None:
            rendered = self._receiver.render(str(chat_id))
            if rendered:
                secciones.insert(0, rendered)

        # Inyectar secciones adicionales en el use case ANTES de execute().
        self._ports.run_agent.set_extra_system_sections([s for s in secciones if s])

        # Identidad del remitente — hay UN único humano emisor que disparó este
        # ``execute()`` (privado, mention/respuesta dirigida al bot en grupo, o
        # voz/foto en grupo, que no pasan por el buffer), así que
        # ``{{CHANNEL.SENDER}}/USERNAME/FIRST_NAME/LAST_NAME}}`` tienen valor unívoco.
        # El flush de grupos (``run_group``) resuelve el sender desde el snapshot
        # del último emisor humano del batch.
        sender_name: str | None = None
        sender_username: str | None = None
        sender_first_name: str | None = None
        sender_last_name: str | None = None
        from_user = getattr(message, "from_user", None)
        if from_user is not None:
            sender_username = _safe_optional_str(getattr(from_user, "username", None))
            sender_first_name = _safe_optional_str(getattr(from_user, "first_name", None))
            sender_last_name = _safe_optional_str(getattr(from_user, "last_name", None))
        sender_name = _safe_optional_str(compose_sender_identity(message))

        turn_ctx = ChannelContext(
            channel_type="telegram",
            user_id=str(user.id),
            chat_id=str(chat_id),
            sender_name=sender_name,
            username=sender_username,
            first_name=sender_first_name,
            last_name=sender_last_name,
        )
        # En grupos NO usamos intermediate_sink: los intermedios del LLM (texto que
        # acompaña tool_calls) se emitirían directo al chat vía sink y NO se incluirían
        # en el ``response`` final → el broadcast saldría con texto vacío/residual y
        # los otros bots del grupo no verían la respuesta. Alineado con run_group.
        live_sink: IIntermediateSink | None = (
            OutboundIntermediateSink(self._ports.channel_outbound, str(chat_id))
            if not es_grupo and self._ports.channel_outbound is not None
            else None
        )
        # In-flight-message-injection: para chats PRIVADOS, si ya hay un turno
        # corriendo en este scope, persistimos el mensaje y ACK rápido. El loop
        # en curso drenará el mensaje entre iteraciones via history.db.
        # Para GRUPOS mantenemos el flow legacy: ya tienen su propio buffer-delay
        # vía schedule_flush + record_user_message, y la inyección in-flight no
        # aplica (SCN-IFI-13/14 del spec).
        agent_id = self._ports.run_agent.get_agent_info().id
        scope = (agent_id, "telegram", str(chat_id))
        skip_marker_value = SKIP_MARKER if (self._behavior == "autonomous" and es_grupo) else None

        # Si es grupo o si user_input is None (modo history-derived: foto enriquecida)
        # → saltar el branch in-flight y caer en el flow legacy.
        use_inflight_routing = not es_grupo and user_input is not None

        # El scope (channel, chat_id) se deriva de turn_ctx dentro de execute —
        # una sola fuente de verdad, sin estado compartido entre turnos.
        async def _ejecutar_turno() -> str:
            return await self._ports.run_agent.execute(
                user_input,
                intermediate_sink=live_sink,
                ctx=turn_ctx,
                skip_marker=skip_marker_value,
            )

        try:
            if use_inflight_routing:
                assert user_input is not None  # narrowing: ver use_inflight_routing
                result = await dispatch_inbound_turn(
                    scope_registry=self._ports.scope_registry,
                    history=self._ports.history,
                    scope=scope,
                    message=user_input,
                    execute=_ejecutar_turno,
                )
                if not result.executed:
                    # Scope ocupado por otro turno: el helper ya persistió el
                    # mensaje; acá solo va el ACK rápido al chat.
                    await message.reply_text(result.reply)
                    return
                response = result.reply
            else:
                # Flow legacy (grupo o history-derived): sin slot, turno directo.
                response = await _ejecutar_turno()

            # Marcador __SKIP__ — solo aplica en modo autónomo en grupos. Detección
            # TOLERANTE: puede aparecer en cualquier parte de la respuesta (los LLMs
            # suelen agregar pre/post-amble). Cualquier ocurrencia suprime el envío
            # y el broadcast; el use case aplica la misma regla para no persistir.
            if self._behavior == "autonomous" and es_grupo and is_skip_response(response):
                logger.debug(
                    "autonomous_skip detectado (agent=%s, chat_id=%s)", self._agent_id, chat_id
                )
                return

            await send_html_or_plain(
                lambda text, pm: message.reply_text(text, parse_mode=pm), response
            )

            # Emitir broadcast DESPUÉS del reply, solo para grupos, fire-and-forget.
            if es_grupo:
                asyncio.ensure_future(
                    self._egress.emit(
                        event_type="assistant_response", chat_id=str(chat_id), content=response
                    )
                )

        except Exception as exc:
            # Blip de red transitorio entregando la respuesta (TimedOut /
            # ConnectTimeout). El turno YA corrió y la respuesta YA está persistida
            # en history.db: otro reply_text sería otro fallo y le mostraría al
            # usuario "Error: Timed out" sobre contenido que SÍ se guardó.
            # ``BadRequest`` hereda de ``NetworkError`` pero es un request
            # malformado (bug nuestro) → cae al manejo de error real de abajo.
            if isinstance(exc, NetworkError) and not isinstance(exc, BadRequest):
                logger.warning(
                    "Telegram '%s': error de red entregando la respuesta "
                    "(ya persistida en history.db), se ignora: %s",
                    self._agent_id,
                    exc,
                )
                return
            logger.exception("Error procesando mensaje Telegram para '%s'", self._agent_id)
            await message.reply_text(f"Error: {exc}")
            await self._reactions.react(update, "👎")
        finally:
            # El slot del scope registry lo libera dispatch_inbound_turn en su
            # propio finally — acá solo queda la limpieza de extra_sections
            # para no contaminar el turno siguiente.
            self._ports.run_agent.set_extra_system_sections([])

    async def run_group(
        self, chat_id_str: str, chat_type: str, last_sender: dict[str, str | None]
    ) -> None:
        """Flush de un grupo: construye la respuesta desde el historial y la envía.

        Inyecta contexto de broadcast vía ``receiver.render`` y, en modo
        autónomo, la sección ``__SKIP__`` que permite al LLM optar por silencio.
        ``last_sender`` es el snapshot del último emisor humano del chat (vacío
        si no hubo: los cuatro campos quedan en ``None`` y las variables
        ``{{CHANNEL.SENDER}}/...`` se dejan literales).
        """
        chat_id_int = int(chat_id_str)
        secciones: list[str] = []

        if self._receiver is not None:
            rendered = self._receiver.render(chat_id_str)
            if rendered:
                secciones.append(rendered)

        if self._behavior == "autonomous":
            secciones.append(_AUTONOMOUS_SECTION)

        self._ports.run_agent.set_extra_system_sections([s for s in secciones if s])
        turn_ctx = ChannelContext(
            channel_type="telegram",
            user_id=self._agent_id,
            chat_id=chat_id_str,
            sender_name=last_sender.get("sender_name"),
            username=last_sender.get("username"),
            first_name=last_sender.get("first_name"),
            last_name=last_sender.get("last_name"),
        )
        try:
            # Scope (channel, chat_id) derivado de turn_ctx dentro de execute.
            response = await self._ports.run_agent.execute(
                ctx=turn_ctx,
                skip_marker=SKIP_MARKER if self._behavior == "autonomous" else None,
            )

            if not response:
                # execute() devolvió vacío — historial sin trailing role=user.
                # Puede pasar si otro flush concurrente ya consumió el batch.
                return

            if self._behavior == "autonomous" and is_skip_response(response):
                logger.debug(
                    "autonomous_skip detectado (agent=%s, chat_id=%s)",
                    self._agent_id,
                    chat_id_str,
                )
                return

            await send_html_or_plain(
                lambda text, pm: self._app.bot.send_message(
                    chat_id=chat_id_int, text=text, parse_mode=pm
                ),
                response,
            )

            asyncio.ensure_future(
                self._egress.emit(
                    event_type="assistant_response", chat_id=chat_id_str, content=response
                )
            )

        except Exception as exc:
            # Mismo criterio que ``run``: el blip de red se loguea, no se responde.
            if isinstance(exc, NetworkError) and not isinstance(exc, BadRequest):
                logger.warning(
                    "Telegram '%s': error de red entregando la respuesta al grupo "
                    "(ya persistida en history.db), se ignora: %s",
                    self._agent_id,
                    exc,
                )
                return
            logger.exception(
                "Error procesando flush de grupo (agent=%s, chat_id=%s)",
                self._agent_id,
                chat_id_str,
            )
            try:
                await self._app.bot.send_message(chat_id=chat_id_int, text=f"Error: {exc}")
            except Exception:
                pass
        finally:
            self._ports.run_agent.set_extra_system_sections([])
