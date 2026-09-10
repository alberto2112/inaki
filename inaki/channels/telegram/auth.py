"""Autorización del bot de Telegram: quién puede hablarle y desde dónde.

Objeto puro (sin PTB, sin I/O): recibe las dos listas de la config y responde
la matriz por contexto. Lo consumen los handlers de mensaje, el ingress de
broadcast (que llega sin ``Update``) y el aviso de vuelta online.
"""

from __future__ import annotations

from telegram import Update

from inaki.channels.telegram.message_mapper import _TIPOS_GRUPO


class TelegramAuth:
    """Matriz de autorización por contexto del mensaje.

    - Privado: el ``user_id`` debe estar en ``allowed_user_ids`` (lista vacía = todos).
    - Grupo: el ``chat_id`` debe estar en ``allowed_chat_ids`` (lista vacía = el bot
      NO responde en grupos); ``allowed_user_ids`` NO aplica en grupos.
    """

    def __init__(
        self, allowed_user_ids: tuple[str, ...], allowed_chat_ids: tuple[str, ...]
    ) -> None:
        self.allowed_user_ids: list[str] = list(allowed_user_ids)
        self.allowed_chat_ids: list[str] = list(allowed_chat_ids)

    def is_allowed(self, user_id: int) -> bool:
        """Admin-only: la regla de los comandos slash y de los chats privados."""
        if not self.allowed_user_ids:
            return True
        return str(user_id) in self.allowed_user_ids

    def is_allowed_chat(self, chat_id: int | str) -> bool:
        """Solo grupos: ``allowed_chat_ids`` es la única fuente de "dónde vive el bot"."""
        return str(chat_id) in self.allowed_chat_ids

    def is_authorized(self, update: Update) -> bool:
        """Guardián único de los handlers de mensaje (texto, foto, voz, media).

        Un update sin emisor o sin chat (defensivo) se rechaza. Los comandos
        slash NO pasan por acá: siguen siendo admin-only vía ``is_allowed``.
        """
        chat = update.effective_chat
        user = update.effective_user
        if chat is None or user is None:
            return False
        if chat.type in _TIPOS_GRUPO:
            return self.is_allowed_chat(chat.id)
        return self.is_allowed(user.id)
