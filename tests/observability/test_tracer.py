"""``JsonlTurnTracer``: escribe por agente, recorta lo largo y JAMÁS rompe el turno."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from inaki.observability import JsonlTurnTracer


def _eventos(path: Path) -> list[dict]:
    return [json.loads(linea) for linea in path.read_text(encoding="utf-8").splitlines()]


def test_escribe_una_linea_por_evento_con_el_contexto_bindeado(tmp_path: Path) -> None:
    tracer = JsonlTurnTracer(tmp_path / "turns")
    turno = tracer.bind(agent_id="asistente", turn_id="abc123", channel="cli")

    turno.trace("turn.start", user_input="hola")
    turno.trace("turn.end", response="chau")

    eventos = _eventos(tmp_path / "turns" / "asistente.jsonl")
    assert [e["event"] for e in eventos] == ["turn.start", "turn.end"]
    assert eventos[0]["agent_id"] == "asistente"
    assert eventos[0]["turn_id"] == "abc123"
    assert eventos[0]["channel"] == "cli"
    assert eventos[0]["user_input"] == "hola"
    assert "ts" in eventos[0]


def test_bind_no_muta_al_padre(tmp_path: Path) -> None:
    padre = JsonlTurnTracer(tmp_path)
    padre.bind(agent_id="a").trace("x")
    padre.bind(agent_id="b").trace("y")

    assert (tmp_path / "a.jsonl").exists() and (tmp_path / "b.jsonl").exists()
    assert not (tmp_path / "sin-agente.jsonl").exists()


def test_recorta_strings_largos_tambien_anidados(tmp_path: Path) -> None:
    tracer = JsonlTurnTracer(tmp_path, max_field_chars=10).bind(agent_id="a")

    tracer.trace("turn.prompt", system_prompt="x" * 50, messages=[{"content": "y" * 50}])

    evento = _eventos(tmp_path / "a.jsonl")[0]
    assert evento["system_prompt"].startswith("xxxxxxxxxx… [recortado, 50 chars]")
    assert evento["messages"][0]["content"].startswith("yyyyyyyyyy… [recortado")


def test_un_fallo_de_escritura_no_propaga_y_avisa_una_sola_vez(tmp_path: Path, caplog) -> None:
    bloqueado = tmp_path / "fichero"
    bloqueado.write_text("no soy un directorio")
    tracer = JsonlTurnTracer(bloqueado / "turns").bind(agent_id="a")

    with caplog.at_level(logging.WARNING):
        tracer.trace("turn.start")
        tracer.trace("turn.end")

    avisos = [r for r in caplog.records if "Traza de turno deshabilitada" in r.getMessage()]
    assert len(avisos) == 1
