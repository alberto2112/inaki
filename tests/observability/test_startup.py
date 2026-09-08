"""``startup_event``: una línea uniforme por recurso, filtrable por ``event``."""

from __future__ import annotations

import logging

from inaki.observability import startup_event


def test_ok_y_skip_son_info_error_es_error(caplog) -> None:
    logger = logging.getLogger("prueba.startup")
    with caplog.at_level(logging.INFO):
        startup_event(logger, "broadcast", status="ok", agent="a", role="server", port=6499)
        startup_event(logger, "broadcast", status="skip", agent="b", reason="enabled=false")
        startup_event(logger, "telegram_bot", status="error", agent="c", reason="token inválido")

    niveles = [r.levelname for r in caplog.records]
    assert niveles == ["INFO", "INFO", "ERROR"]

    ok, skip, error = caplog.records
    assert ok.event == "startup.resource" and ok.resource == "broadcast" and ok.status == "ok"
    assert ok.agent == "a" and ok.role == "server" and ok.port == 6499
    assert skip.reason == "enabled=false"
    assert error.reason == "token inválido"
    assert all(r.getMessage().startswith("[startup] ") for r in caplog.records)
