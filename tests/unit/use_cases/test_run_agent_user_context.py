"""Tests unitarios para ``RunAgentUseCase._read_user_context``.

Cubre la resolución del archivo per-entidad (memoria caliente). Capas
concatenadas (la que falte se omite):

  0. ``~/.inaki/users/{channel_type}/_common.md`` (común al canal, antes del específico)
  1. ``~/.inaki/users/{channel_type}/{context_id}.md`` donde
     ``context_id = chat_id or user_id`` — MISMA clave en privado y en grupo, sin
     ramas separadas ni fallback a ``username`` (la clave es la conversación, no la
     persona; ver ``ChannelContext.context_id``)
  2. ``""`` (sin contexto)

Casos especiales:
  - ``ctx=None`` (turno sin ChannelContext, ej: scheduler triggers) → ``""``
  - ``context_id`` con separadores de path o ``..`` → se descarta → ``""``
  - Scope por canal: misma key en distintos canales = archivos distintos
"""

from __future__ import annotations

import pytest

from core.domain.value_objects.channel_context import ChannelContext
from core.use_cases.run_agent import RunAgentUseCase
from infrastructure.container import build_run_agent_settings


@pytest.fixture
def users_root(tmp_path):
    """Fija el home de instancia a un tmp y crea ``users/`` vacío. Devuelve ``users/``.

    Usa ``set_inaki_home`` (no ``HOME``): ``get_inaki_home()`` resuelve por override →
    ``INAKI_HOME`` → default, nunca por ``HOME``. Resetea el override (process-global) al final."""
    from infrastructure.home import set_inaki_home

    home = tmp_path / ".inaki"
    root = home / "users"
    root.mkdir(parents=True)
    set_inaki_home(home)
    yield root
    set_inaki_home(None)


@pytest.fixture
def use_case(
    users_root,
    agent_config,
    mock_llm,
    mock_memory,
    mock_embedder,
    mock_skills,
    mock_history,
    mock_tools,
):
    """RunAgentUseCase estándar — el ctx del turno se pasa como argumento.

    Depende de ``users_root`` para que el home quede fijado ANTES de construir los
    settings (``build_run_agent_settings`` resuelve ``users_dir`` vía ``get_inaki_home()``)."""
    return RunAgentUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        settings=build_run_agent_settings(agent_config),
    )


def test_devuelve_vacio_si_ctx_es_none(use_case):
    """``ctx=None`` (turno sin ChannelContext) → ``""`` (defensivo, no rompe)."""
    assert use_case._read_user_context(None) == ""


def test_lee_por_chat_id(use_case, users_root):
    """Con ``chat_id`` seteado, la clave es el chat_id (context_id = chat_id or user_id)."""
    (users_root / "telegram").mkdir()
    (users_root / "telegram" / "-100123.md").write_text("ctx del chat", encoding="utf-8")

    ctx = ChannelContext(channel_type="telegram", user_id="42", chat_id="-100123")

    assert use_case._read_user_context(ctx) == "ctx del chat"


def test_fallback_a_user_id_sin_chat_id(use_case, users_root):
    """Sin ``chat_id`` (CLI/REST/daemon), la clave cae a ``user_id``."""
    (users_root / "cli").mkdir()
    (users_root / "cli" / "local.md").write_text("ctx por user_id", encoding="utf-8")

    ctx = ChannelContext(channel_type="cli", user_id="local")

    assert use_case._read_user_context(ctx) == "ctx por user_id"


def test_username_ya_no_participa_del_lookup(use_case, users_root):
    """El ``username`` ya NO se consulta: la clave es la conversación, no la persona.

    Antes (pre ``context_id``) un privado leía ``{username}.md``. Ahora solo cuenta
    ``context_id`` (chat_id or user_id) — un archivo nombrado por username queda
    inerte. Documenta el breaking change hacia la clave única.
    """
    (users_root / "telegram").mkdir()
    (users_root / "telegram" / "alberto.md").write_text("no debería leerse", encoding="utf-8")

    ctx = ChannelContext(
        channel_type="telegram", user_id="999", chat_id="555", username="alberto"
    )

    # context_id = "555" → no existe 555.md → "" (alberto.md se ignora)
    assert use_case._read_user_context(ctx) == ""


def test_devuelve_vacio_si_ningun_archivo_existe(use_case, users_root):
    """Sin archivo por ``context_id`` → ``""`` (no rompe ni log noisy)."""
    ctx = ChannelContext(channel_type="telegram", user_id="999", chat_id="123")

    assert use_case._read_user_context(ctx) == ""


def test_scope_por_canal_no_cruza_canales(use_case, users_root):
    """Misma key en distintos canales = archivos distintos (scope por channel_type)."""
    (users_root / "telegram").mkdir()
    (users_root / "cli").mkdir()
    (users_root / "telegram" / "42.md").write_text("telegram-context", encoding="utf-8")
    (users_root / "cli" / "42.md").write_text("cli-context", encoding="utf-8")

    ctx_telegram = ChannelContext(channel_type="telegram", user_id="x", chat_id="42")
    ctx_cli = ChannelContext(channel_type="cli", user_id="42")

    assert use_case._read_user_context(ctx_telegram) == "telegram-context"
    assert use_case._read_user_context(ctx_cli) == "cli-context"


def test_common_md_antes_del_archivo_por_context_id(use_case, users_root):
    """``_common.md`` (común al canal) se concatena ANTES del archivo per-entidad."""
    (users_root / "telegram").mkdir()
    (users_root / "telegram" / "_common.md").write_text("no uses tablas markdown", encoding="utf-8")
    (users_root / "telegram" / "-100.md").write_text("ctx entidad", encoding="utf-8")

    ctx = ChannelContext(channel_type="telegram", user_id="x", chat_id="-100")

    assert use_case._read_user_context(ctx) == "no uses tablas markdown\n\nctx entidad"


def test_common_md_solo_sin_archivo_especifico(use_case, users_root):
    """Si hay ``_common.md`` pero ningún archivo per-entidad, devuelve solo las instrucciones."""
    (users_root / "telegram").mkdir()
    (users_root / "telegram" / "_common.md").write_text("formato común del canal", encoding="utf-8")

    ctx = ChannelContext(channel_type="telegram", user_id="x", chat_id="sin-archivo")

    assert use_case._read_user_context(ctx) == "formato común del canal"


def test_common_md_scopeado_por_canal(use_case, users_root):
    """``_common.md`` es por canal — telegram no hereda las de cli."""
    (users_root / "telegram").mkdir()
    (users_root / "cli").mkdir()
    (users_root / "cli" / "_common.md").write_text("instr cli", encoding="utf-8")

    ctx_telegram = ChannelContext(channel_type="telegram", user_id="x", chat_id="1")

    # telegram no tiene _common.md ni archivo per-entidad → vacío
    assert use_case._read_user_context(ctx_telegram) == ""


def test_grupo_y_privado_resuelven_igual(use_case, users_root):
    """El objetivo del cambio: grupo y privado usan la MISMA resolución (chat_id).

    Un grupo (chat_id negativo) y un privado (chat_id positivo) leen cada uno su
    ``{chat_id}.md`` — sin ramas separadas, sin heurística de último emisor.
    """
    (users_root / "telegram").mkdir()
    (users_root / "telegram" / "-100.md").write_text("ctx grupo", encoding="utf-8")
    (users_root / "telegram" / "555.md").write_text("ctx privado", encoding="utf-8")

    grupo = ChannelContext(channel_type="telegram", user_id="inaki", chat_id="-100")
    privado = ChannelContext(channel_type="telegram", user_id="555", chat_id="555")

    assert use_case._read_user_context(grupo) == "ctx grupo"
    assert use_case._read_user_context(privado) == "ctx privado"


@pytest.mark.parametrize("malicious", ["../etc", "a/b", "a\\b", ".."])
def test_rechaza_separadores_en_context_id(use_case, users_root, malicious):
    """``context_id`` con separadores de path o ``..`` se descarta (anti-traversal)."""
    ctx = ChannelContext(channel_type="telegram", user_id="inaki", chat_id=malicious)

    assert use_case._read_user_context(ctx) == ""


def test_rechaza_separadores_en_user_id_como_fallback(use_case, users_root):
    """Sin ``chat_id``, si el ``user_id`` (=context_id) trae separadores, se descarta → ``""``."""
    ctx = ChannelContext(channel_type="cli", user_id="../etc/passwd")

    assert use_case._read_user_context(ctx) == ""
