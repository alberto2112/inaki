"""Camino dorado 1: del YAML en disco a un turno persistido.

Protege el contrato que las fases 2 (config), 7 (memory/tools) y 9 (app) van a
reescribir por dentro: "dado un home con ``global.yaml`` + ``agents/x.yaml``, el
composition root construye un agente que responde y deja rastro en ``history.db``,
y el ciclo de vida arranca y se apaga sin error".
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from core.domain.value_objects.channel_context import ChannelContext

from .conftest import AGENT_ID, RESPUESTA_LLM, USER_ID, FakeLLM


def _filas_historial(home: Path) -> list[tuple[str, str, str, str]]:
    with sqlite3.connect(home / "data" / "history.db") as conn:
        return conn.execute(
            "SELECT role, content, channel, chat_id FROM history ORDER BY id"
        ).fetchall()


async def test_yaml_a_turno_persistido(home: Path, app_container, bordes_externos: FakeLLM):
    agente = app_container.get_agent(AGENT_ID)
    ctx = ChannelContext(channel_type="cli", user_id=USER_ID, chat_id=USER_ID)

    respuesta = await agente.run_agent.execute("hola, ¿quién sos?", ctx=ctx)

    assert respuesta == RESPUESTA_LLM
    assert len(bordes_externos.llamadas) == 1
    assert "asistente de prueba" in bordes_externos.llamadas[0]["system_prompt"]

    filas = _filas_historial(home)
    assert [(rol, canal, chat) for rol, _, canal, chat in filas] == [
        ("user", "cli", USER_ID),
        ("assistant", "cli", USER_ID),
    ]
    assert filas[0][1] == "hola, ¿quién sos?"
    assert filas[1][1] == RESPUESTA_LLM


async def test_ciclo_de_vida_arranca_y_apaga(home: Path, app_container):
    await app_container.startup()
    try:
        assert (home / "data" / "scheduler.db").exists()
    finally:
        await app_container.shutdown()


def test_los_paths_de_runtime_se_anclan_al_home(home: Path, app_container):
    """El home relocaliza TODO: ningún path efectivo escapa de ``tmp_path``."""
    cfg = app_container.get_agent(AGENT_ID).agent_config
    for path in (
        cfg.chat_history.db_filename,
        cfg.memories.db_filename,
        cfg.embedding.cache_filename,
        app_container.global_config.scheduler.db_filename,
    ):
        assert Path(path).is_relative_to(home), path
