"""Guard de la Fase 4: la config que está mal se hace notar.

Cada test de acá cubre un fallo que ANTES era silencioso — un default puesto por
detrás, un agente que desaparecía del registry, un typo tragado. El invariante
del repo es que un arranque que no puede fallar es un arranque que no se puede
diagnosticar; estos son sus casos concretos en el subsistema de config.

La contracara también se testea: lo que se degrada a propósito (el stack de
visión) tiene que seguir degradando, y decir qué capacidad queda muda.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from core.domain.errors import ConfigError
from infrastructure.config import (
    LLMConfig,
    MemoriesConfig,
    SchedulerConfig,
    TelegramChannelConfig,
    load_agent_config,
    load_global_config,
)


# ---------------------------------------------------------------------------
# Claves desconocidas: el typo se nombra y se sugiere el campo correcto
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "modelo, typo, esperado",
    [
        (LLMConfig, "temperatura", "temperature"),
        (LLMConfig, "timeout", "timeout_seconds"),
        (TelegramChannelConfig, "reaction", "reactions"),
        (SchedulerConfig, "enable", "enabled"),
    ],
)
def test_una_clave_parecida_sugiere_la_correcta(modelo: type, typo: str, esperado: str) -> None:
    with pytest.raises(ValidationError) as exc:
        modelo(**{typo: "x"})

    mensaje = str(exc.value)
    assert typo in mensaje
    assert f"¿Quisiste decir '{esperado}'?" in mensaje


def test_una_clave_sin_parecido_lista_las_validas() -> None:
    with pytest.raises(ValidationError) as exc:
        LLMConfig(zzz_inventado=1)  # type: ignore[call-arg]

    mensaje = str(exc.value)
    assert "zzz_inventado" in mensaje
    assert "Claves válidas:" in mensaje
    assert "temperature" in mensaje


def test_todo_el_schema_rechaza_lo_desconocido() -> None:
    """No es caso por caso: la base lo aplica a los 37 modelos."""
    import inspect

    from pydantic import BaseModel

    import infrastructure.config_schema as schema

    modelos = [
        c
        for _, c in inspect.getmembers(schema, inspect.isclass)
        if issubclass(c, BaseModel) and c.__module__ == schema.__name__
    ]
    sin_forbid = [c.__name__ for c in modelos if c.model_config.get("extra") != "forbid"]

    assert not sin_forbid, f"modelos que aún tragan claves desconocidas: {sin_forbid}"


def test_el_bloque_anidado_tambien_valida() -> None:
    """El rechazo no se queda en el nivel top: baja por los sub-modelos."""
    with pytest.raises(ValidationError, match="consolidation"):
        MemoriesConfig(consolidation={"enable": True})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Un agente inválido aborta el arranque
# ---------------------------------------------------------------------------


def _home(tmp_path: Path, agente: str) -> tuple[Path, Path]:
    cfg_dir, agents_dir = tmp_path / "config", tmp_path / "agents"
    cfg_dir.mkdir()
    agents_dir.mkdir()
    (cfg_dir / "global.yaml").write_text(
        "app: {name: Test}\n"
        "llm: {provider: groq, model: m}\n"
        "embedding: {provider: e5_onnx, model_dirname: /tmp/m}\n"
        "memories: {db_filename: ':memory:'}\n"
        "chat_history: {db_filename: /tmp/h.db}\n",
        encoding="utf-8",
    )
    (agents_dir / "dev.yaml").write_text(agente, encoding="utf-8")
    return cfg_dir, agents_dir


def test_un_typo_en_el_agente_aborta_nombrando_el_fichero(tmp_path: Path) -> None:
    cfg_dir, agents_dir = _home(
        tmp_path, "id: dev\nname: Dev\ndescription: d\nllm:\n  temperatura: 0.9\n"
    )
    _, global_raw = load_global_config(cfg_dir)

    with pytest.raises(ConfigError) as exc:
        load_agent_config("dev", agents_dir, global_raw)

    mensaje = str(exc.value)
    assert "dev" in mensaje
    assert "dev.yaml" in mensaje, "el operador necesita saber qué fichero abrir"
    assert "temperatura" in mensaje


def test_un_agente_que_no_existe_sigue_devolviendo_none(tmp_path: Path) -> None:
    """No confundir "no está" con "está roto": preguntar por un agente
    inexistente es legítimo y no debe abortar nada."""
    cfg_dir, agents_dir = _home(tmp_path, "id: dev\nname: Dev\ndescription: d\n")
    _, global_raw = load_global_config(cfg_dir)

    assert load_agent_config("fantasma", agents_dir, global_raw) is None


def test_un_typo_en_el_global_tambien_aborta(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "global.yaml").write_text("llm:\n  max_token: 100\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="max_token"):
        load_global_config(cfg_dir)


# ---------------------------------------------------------------------------
# Lo que se degrada a propósito sigue degradando
# ---------------------------------------------------------------------------


def test_el_wiring_de_fotos_degrada_y_dice_que_capacidad_queda_muda(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """El stack de visión es una dependencia externa pesada: que falte no puede
    tumbar el daemon entero, pero el operador tiene que enterarse de QUÉ perdió."""
    from unittest.mock import MagicMock

    from infrastructure.container import AgentContainer

    self_ = MagicMock()
    self_._photos_wired = False
    self_.agent_config.id = "inaki"
    # El describer de escena es lo que puede faltar en el host (modelo ONNX,
    # InsightFace): que reviente ahí es el caso real que se degrada.
    self_._build_scene_describer.side_effect = RuntimeError("falta el modelo ONNX")

    global_config = MagicMock()
    global_config.photos.enabled = True

    with caplog.at_level("ERROR"):
        AgentContainer.wire_photos(self_, MagicMock(), MagicMock(), global_config)

    assert self_._photos_wired is True, "la degradación no debe reintentar en loop"
    assert "DESHABILITADO" in caplog.text, (
        f"el operador tiene que leer QUÉ capacidad perdió; dijo: {caplog.text!r}"
    )
    assert "inaki" in caplog.text, "y de qué agente"
    assert "falta el modelo ONNX" in caplog.text, "y la causa original"
