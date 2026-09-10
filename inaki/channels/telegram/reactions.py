"""Reacciones del bot (👀 al recibir, 👎 al fallar): opcionales y silenciosas.

Dos flags de config: ``channels.telegram.reactions`` para privados y voz, y
``groups.reactions`` (ya resuelto por el builder: override o herencia) para
grupos. Un fallo al reaccionar NUNCA bloquea el handler.
"""

from __future__ import annotations

from telegram import Update


class Reactions:
    def __init__(self, *, private: bool, groups: bool) -> None:
        self.private = private
        self.groups = groups

    async def react(self, update: Update, emoji: str) -> None:
        """Chats privados y voz. Para grupos usar ``react_group``."""
        if self.private:
            await self._set(update, emoji)

    async def react_group(self, update: Update, emoji: str) -> None:
        if self.groups:
            await self._set(update, emoji)

    @staticmethod
    async def _set(update: Update, emoji: str) -> None:
        message = update.message
        if message is None:
            return
        try:
            await message.set_reaction(emoji)
        except Exception:
            pass  # Reacciones opcionales — no deben bloquear el handler.
