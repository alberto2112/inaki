from __future__ import annotations

from pydantic import BaseModel


class DelegationResult(BaseModel):
    status: str
    summary: str
    details: str | None = None
    reason: str | None = None
    # Solo con ``status="needs_input"``: la UNICA pregunta con la que el hijo pausa y que
    # el padre (o el usuario) contesta con ``delegate(resume=...)``. Obligatoria en ese
    # status; el contrato que la exige (y degrada a ``result_parse_error`` si falta o está
    # en blanco) vive en ``agents/delegation/result_contract.py``.
    question: str | None = None
