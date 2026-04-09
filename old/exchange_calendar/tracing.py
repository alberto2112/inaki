"""
Trace des appels à la tool exchange_calendar.

Réservé : l'ancien traçage NDJSON via les skills a été retiré. Réactiver ici
une écriture fichier si besoin (ex. derrière LOG_LEVEL=DEBUG).
"""

from typing import Any, Dict, Optional


def append_exchange_llm_bridge(
    _operation: str,
    _kwargs_in: Dict[str, Any],
    _result: Optional[Any],
) -> None:
    """No-op : ancien hook de trace skill ; conserver la signature pour l'engine."""
