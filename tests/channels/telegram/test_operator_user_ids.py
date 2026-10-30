"""``operator_user_ids``: la lista de operadores, separada de ``allowed_user_ids``.

Antes las dos cosas (quién puede HABLAR por privado y quién es OPERADOR) colgaban de
la misma lista, y "bot abierto para hablar, solo el dueño como operador" no se podía
expresar. La tabla de verdad (chat privado) es el contrato; cada fila es un caso:

| allowed_user_ids | operator_user_ids | habla  | operador        |
|------------------|-------------------|--------|-----------------|
| vacía            | vacía             | todos  | nadie           |
| vacía            | [yo]              | todos  | yo              |
| [a, b]           | vacía             | a, b   | a, b (hereda)   |
| [a, b]           | [a]               | a, b   | a               |
| [a]              | [c]               | —      | error al cargar |

El error se prueba cargando un YAML REAL con el loader y pasándolo por el borde de
config: instanciar el modelo no demuestra que el operador lo lea.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from inaki.channels.telegram.auth import TelegramAuth
from inaki.channels.telegram.config import TelegramChannelConfig
from inaki.channels.telegram.wiring import build_telegram_channel_settings
from inaki.config import load_agent_config, load_global_config
from inaki.config.boundary import EXIT_CONFIG, borde_de_config
from inaki.shared.errors import ConfigError

A, B, C, YO = 11, 22, 33, 99


def _auth(allowed: list[int], operators: list[int]) -> TelegramAuth:
    """Config → settings del bot → auth: el mismo camino que recorre el arranque."""
    vo = build_telegram_channel_settings(
        TelegramChannelConfig(token="T", allowed_user_ids=allowed, operator_user_ids=operators)
    )
    return TelegramAuth(vo.allowed_user_ids, vo.allowed_chat_ids, vo.operator_user_ids)


# ---------------------------------------------------------------------------
# Tabla de verdad — filas 1 a 4 (la 5 es un error de carga, más abajo)
# ---------------------------------------------------------------------------


def test_fila_1_ambas_vacias_todos_hablan_y_nadie_es_operador() -> None:
    auth = _auth([], [])

    assert auth.is_allowed(A) and auth.is_allowed(B) and auth.is_allowed(YO)
    assert not auth.is_operator(A, "private")
    assert not auth.is_operator(YO, "private")


def test_fila_2_abierto_para_hablar_y_solo_el_dueno_es_operador() -> None:
    """El caso real que motivó el campo: antes era inexpresable."""
    auth = _auth([], [YO])

    assert auth.is_allowed(A) and auth.is_allowed(B) and auth.is_allowed(YO)
    assert auth.is_operator(YO, "private")
    assert not auth.is_operator(A, "private")
    assert not auth.is_operator(B, "private")


def test_fila_3_operadores_vacia_hereda_allowed_user_ids() -> None:
    """El comportamiento de siempre: no hay que migrar ningún YAML."""
    auth = _auth([A, B], [])

    assert auth.is_allowed(A) and auth.is_allowed(B) and not auth.is_allowed(C)
    assert auth.is_operator(A, "private")
    assert auth.is_operator(B, "private")
    assert not auth.is_operator(C, "private")


def test_fila_4_operadores_explicitos_son_un_subconjunto_de_los_que_hablan() -> None:
    auth = _auth([A, B], [A])

    assert auth.is_allowed(A) and auth.is_allowed(B) and not auth.is_allowed(C)
    assert auth.is_operator(A, "private")
    assert not auth.is_operator(B, "private"), "B habla pero ya no hereda el privilegio"
    assert not auth.is_operator(C, "private")


# ---------------------------------------------------------------------------
# En grupos nunca hay operador, con cualquiera de las dos listas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tipo", ["group", "supergroup", "channel"])
def test_en_grupos_nunca_hay_operador(tipo: str) -> None:
    explicito = _auth([], [YO])
    heredado = _auth([A], [])

    assert not explicito.is_operator(YO, tipo)
    assert not heredado.is_operator(A, tipo)
    # Control: el mismo usuario SÍ es operador en privado.
    assert explicito.is_operator(YO, "private") and heredado.is_operator(A, "private")


# ---------------------------------------------------------------------------
# `is_allowed` no cambia: los operadores no hablan "de más"
# ---------------------------------------------------------------------------


def test_is_allowed_no_mira_operator_user_ids() -> None:
    """Con ``allowed`` no vacía el validator exige que operadores ⊆ allowed, así que el
    único hueco posible es construir la auth a mano: ahí ``is_allowed`` sigue
    gobernado SOLO por ``allowed_user_ids``."""
    auth = TelegramAuth(("1",), (), ("2",))

    assert auth.is_allowed(1) and not auth.is_allowed(2)


# ---------------------------------------------------------------------------
# Wiring: config → VO
# ---------------------------------------------------------------------------


def test_el_wiring_normaliza_los_operadores_a_tupla_de_str() -> None:
    vo = build_telegram_channel_settings(
        TelegramChannelConfig(token="T", allowed_user_ids=[A, B], operator_user_ids=[A])
    )

    assert vo.operator_user_ids == (str(A),)


def test_el_wiring_sin_operadores_deja_la_tupla_vacia_para_heredar() -> None:
    vo = build_telegram_channel_settings(TelegramChannelConfig(token="T", allowed_user_ids=[A]))

    assert vo.operator_user_ids == ()


# ---------------------------------------------------------------------------
# Fila 5 — error al cargar (modelo)
# ---------------------------------------------------------------------------


def test_fila_5_operador_que_no_puede_hablar_es_error_y_nombra_los_ids() -> None:
    with pytest.raises(ValidationError) as exc:
        TelegramChannelConfig(token="T", allowed_user_ids=[A], operator_user_ids=[C])

    mensaje = str(exc.value)
    assert "operator_user_ids" in mensaje and "allowed_user_ids" in mensaje
    assert str(C) in mensaje, "el mensaje nombra el id que sobra"


def test_el_error_nombra_todos_los_ids_que_sobran_y_no_los_validos() -> None:
    with pytest.raises(ValidationError) as exc:
        TelegramChannelConfig(token="T", allowed_user_ids=[A, B], operator_user_ids=[A, C, YO])

    mensaje = str(exc.value)
    # Los sobrantes, en orden y sin A (que sí puede hablar). `allowed_user_ids` también
    # aparece en el mensaje, así que se compara la lista exacta de sobrantes.
    assert f"operator_user_ids [{C}, {YO}]" in mensaje


def test_con_allowed_vacia_no_hay_restriccion_sobre_los_operadores() -> None:
    cfg = TelegramChannelConfig(token="T", allowed_user_ids=[], operator_user_ids=[YO, C])

    assert cfg.operator_user_ids == [YO, C]


def test_operadores_dentro_de_allowed_cargan_sin_error() -> None:
    cfg = TelegramChannelConfig(token="T", allowed_user_ids=[A, B], operator_user_ids=[B])

    assert cfg.operator_user_ids == [B]


# ---------------------------------------------------------------------------
# Fila 5 — error al cargar, por el loader real y el borde de config
# ---------------------------------------------------------------------------


def _escribir_home(tmp_path: Path, telegram_yaml: str) -> tuple[Path, Path]:
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
    (agents_dir / "dev.yaml").write_text(
        "id: dev\nname: Dev\ndescription: d\nchannels:\n  telegram:\n    token: T\n"
        + telegram_yaml,
        encoding="utf-8",
    )
    return cfg_dir, agents_dir


def test_el_loader_entrega_las_dos_listas_de_un_yaml_valido(tmp_path: Path) -> None:
    cfg_dir, agents_dir = _escribir_home(
        tmp_path, "    allowed_user_ids: [11, 22]\n    operator_user_ids: [11]\n"
    )
    _, global_raw = load_global_config(cfg_dir)

    agente = load_agent_config("dev", agents_dir, global_raw)

    assert agente is not None
    tg = agente.canal("telegram", TelegramChannelConfig)
    assert tg is not None
    assert tg.allowed_user_ids == [11, 22]
    assert tg.operator_user_ids == [11]


def test_el_loader_aborta_con_un_operador_que_no_puede_hablar(tmp_path: Path) -> None:
    cfg_dir, agents_dir = _escribir_home(
        tmp_path, "    allowed_user_ids: [11]\n    operator_user_ids: [33]\n"
    )
    _, global_raw = load_global_config(cfg_dir)

    with pytest.raises(ConfigError) as exc:
        load_agent_config("dev", agents_dir, global_raw)

    mensaje = str(exc.value)
    assert "dev.yaml" in mensaje, "el error nombra el fichero a corregir"
    assert "operator_user_ids" in mensaje and "33" in mensaje


def test_el_error_llega_al_operador_por_el_borde_de_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sin traceback y con EXIT_CONFIG: lo que lee el operador cuando el daemon no arranca."""
    cfg_dir, agents_dir = _escribir_home(
        tmp_path, "    allowed_user_ids: [11]\n    operator_user_ids: [33]\n"
    )
    _, global_raw = load_global_config(cfg_dir)

    with pytest.raises(SystemExit) as salida:
        with borde_de_config(str(agents_dir)):
            load_agent_config("dev", agents_dir, global_raw)

    assert salida.value.code == EXIT_CONFIG
    err = capsys.readouterr().err
    assert err.startswith("Error de configuración")
    assert "operator_user_ids" in err and "33" in err
    assert "Traceback" not in err
