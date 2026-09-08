"""``setup_logging``: un solo stack, dos formatos, y los ``extra`` SIEMPRE visibles."""

from __future__ import annotations

import io
import json
import logging

import pytest

from inaki.observability import setup_logging
from inaki.observability.log_setup import _MARCA_HANDLER


@pytest.fixture
def salida():
    buffer = io.StringIO()
    yield buffer
    # Dejar el root como estaba para no ensuciar otros tests.
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, _MARCA_HANDLER, False):
            root.removeHandler(h)


def test_console_publica_hora_nivel_logger_y_extras(salida: io.StringIO) -> None:
    setup_logging("INFO", "console", stream=salida)

    logging.getLogger("prueba.mod").info(
        "broadcast.message.received", extra={"from_agent_id": "otro", "chat_id": "-100"}
    )

    linea = salida.getvalue().strip()
    assert "INFO" in linea and "prueba.mod" in linea
    assert "broadcast.message.received" in linea
    assert "from_agent_id='otro'" in linea and "chat_id='-100'" in linea


def test_json_es_una_linea_parseable_con_extras(salida: io.StringIO) -> None:
    setup_logging("WARNING", "json", stream=salida)

    logging.getLogger("prueba.json").warning("algo pasó", extra={"resource": "broadcast"})

    evento = json.loads(salida.getvalue().strip())
    assert evento["level"] == "WARNING"
    assert evento["logger"] == "prueba.json"
    assert evento["msg"] == "algo pasó"
    assert evento["resource"] == "broadcast"
    assert evento["ts"].endswith("+00:00")


def test_nivel_desconocido_cae_a_info(salida: io.StringIO) -> None:
    setup_logging("VERBOSO", "console", stream=salida)

    assert logging.getLogger().level == logging.INFO


def test_es_idempotente_y_no_toca_handlers_ajenos(salida: io.StringIO) -> None:
    root = logging.getLogger()
    ajeno = logging.NullHandler()
    root.addHandler(ajeno)
    try:
        setup_logging("INFO", "console", stream=salida)
        setup_logging("DEBUG", "json", stream=salida)

        propios = [h for h in root.handlers if getattr(h, _MARCA_HANDLER, False)]
        assert len(propios) == 1
        assert ajeno in root.handlers
        assert root.level == logging.DEBUG
    finally:
        root.removeHandler(ajeno)
