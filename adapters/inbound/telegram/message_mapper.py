"""Mapper entre mensajes de Telegram y entidades del dominio."""

from __future__ import annotations

from telegram import Update

from core.domain.entities.message import Message, Role


def telegram_update_to_input(update: Update) -> str | None:
    """Extrae el texto del mensaje de un Update de Telegram."""
    if update.message and update.message.text:
        return update.message.text.strip()
    return None


def format_response(response: str) -> str:
    """
    Formatea la respuesta del agente para Telegram.
    Telegram soporta Markdown básico (MarkdownV2) pero es estricto con el escape.
    Aquí devolvemos texto plano para máxima compatibilidad.
    """
    return response
