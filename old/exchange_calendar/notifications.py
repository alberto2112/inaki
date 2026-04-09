"""
Notifications Telegram éphémères avant l'exécution de la tool calendrier.

Le flux actuel (tools atomiques) ne fournit pas de canal utilisateur au moment
de l'appel : ce module reste un point d'extension si un adaptateur injecte
un jour un contexte de présentation.
"""

from typing import Any, Dict


async def publier_messages_ephemeres_debut_tour(_kwargs_outil: Dict[str, Any]) -> None:
    """Point d'extension pour statut / messages éphémères ; non câblé dans le flux tool-only."""
