"""Optional tracing hook for exchange_calendar calls (no-op by default)."""

from typing import Any, Dict, Optional


def append_exchange_llm_bridge(
    _operation: str,
    _kwargs_in: Dict[str, Any],
    _result: Optional[Any],
) -> None:
    """Reserved: file/NDJSON tracing can be re-enabled here."""
