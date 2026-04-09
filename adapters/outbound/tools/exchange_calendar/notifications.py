"""Extension point for ephemeral user notifications before calendar tool runs."""

from typing import Any, Dict


async def publier_messages_ephemeres_debut_tour(_kwargs_outil: Dict[str, Any]) -> None:
    """No-op unless an inbound adapter injects a presentation channel."""
