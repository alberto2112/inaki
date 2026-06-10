"""Tests unitarios para ``RunAgentUseCase._read_user_context``.

Cubre la resolución de archivos per-user que reemplazó al `~/.inaki/USER.md`
global. Capas concatenadas (la que falte se omite):

  0. ``~/.inaki/users/{channel_type}/_common.md`` (común al canal, antes del per-user)
  1. ``~/.inaki/users/{channel_type}/{username}.md``
  2. ``~/.inaki/users/{channel_type}/{user_id}.md``
  3. ``""`` (sin contexto)

Casos especiales:
  - ``ctx=None`` (turno sin ChannelContext, ej: scheduler triggers) → ``""``
  - Username con separadores de path o ``..`` → se skippea ese candidato
  - Scope por canal: misma key en distintos canales = archivos distintos
"""

from __future__ import annotations

import pytest

from core.domain.value_objects.channel_context import ChannelContext
from core.use_cases.run_agent import RunAgentUseCase
from infrastructure.container import build_run_agent_settings


@pytest.fixture
def use_case(
    agent_config, mock_llm, mock_memory, mock_embedder, mock_skills, mock_history, mock_tools
):
    """RunAgentUseCase estándar — el ctx del turno se pasa como argumento."""
    return RunAgentUseCase(
        llm=mock_llm,
        memory=mock_memory,
        embedder=mock_embedder,
        skills=mock_skills,
        history=mock_history,
        tools=mock_tools,
        settings=build_run_agent_settings(agent_config),
    )


@pytest.fixture
def users_root(tmp_path, monkeypatch):
    """Redirige ``~`` a un tmp y crea ``users/`` vacío. Devuelve la ruta a ``users/``."""
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".inaki" / "users"
    root.mkdir(parents=True)
    return root


def test_devuelve_vacio_si_ctx_es_none(use_case):
    """``ctx=None`` (turno sin ChannelContext) → ``""`` (defensivo, no rompe)."""
    assert use_case._read_user_context(None) == ""


def test_lee_por_username(use_case, users_root):
    """Si ``users/{channel}/{username}.md`` existe, devuelve su contenido."""
    (users_root / "telegram").mkdir()
    (users_root / "telegram" / "alberto.md").write_text("contexto de alberto", encoding="utf-8")

    ctx = ChannelContext(channel_type="telegram", user_id="999", username="alberto")

    assert use_case._read_user_context(ctx) == "contexto de alberto"


def test_fallback_a_user_id_si_no_hay_archivo_por_username(use_case, users_root):
    """Sin ``users/{ch}/{username}.md`` → cae a ``users/{ch}/{user_id}.md``."""
    (users_root / "telegram").mkdir()
    (users_root / "telegram" / "12345.md").write_text("ctx por id", encoding="utf-8")

    ctx = ChannelContext(channel_type="telegram", user_id="12345", username="sin-archivo")

    assert use_case._read_user_context(ctx) == "ctx por id"


def test_fallback_a_user_id_si_no_hay_username(use_case, users_root):
    """``username=None`` (usuario sin handle) → lookup directo por ``user_id``."""
    (users_root / "telegram").mkdir()
    (users_root / "telegram" / "12345.md").write_text("ctx por id", encoding="utf-8")

    ctx = ChannelContext(channel_type="telegram", user_id="12345")

    assert use_case._read_user_context(ctx) == "ctx por id"


def test_devuelve_vacio_si_ningun_archivo_existe(use_case, users_root):
    """Sin ``username.md`` ni ``user_id.md`` → ``""`` (no rompe ni log noisy)."""
    ctx = ChannelContext(channel_type="telegram", user_id="999", username="desconocido")

    assert use_case._read_user_context(ctx) == ""


def test_scope_por_canal_no_cruza_canales(use_case, users_root):
    """``alberto`` en telegram ≠ ``alberto`` en cli — el lookup es scopeado por canal."""
    (users_root / "telegram").mkdir()
    (users_root / "cli").mkdir()
    (users_root / "telegram" / "alberto.md").write_text("telegram-context", encoding="utf-8")
    (users_root / "cli" / "alberto.md").write_text("cli-context", encoding="utf-8")

    ctx_telegram = ChannelContext(channel_type="telegram", user_id="1", username="alberto")
    ctx_cli = ChannelContext(channel_type="cli", user_id="2", username="alberto")

    assert use_case._read_user_context(ctx_telegram) == "telegram-context"
    assert use_case._read_user_context(ctx_cli) == "cli-context"


def test_inyecta_instructions_antes_del_archivo_per_user(use_case, users_root):
    """``_common.md`` (común al canal) se concatena ANTES del archivo per-user."""
    (users_root / "telegram").mkdir()
    (users_root / "telegram" / "_common.md").write_text("no uses tablas markdown", encoding="utf-8")
    (users_root / "telegram" / "alberto.md").write_text("contexto de alberto", encoding="utf-8")

    ctx = ChannelContext(channel_type="telegram", user_id="999", username="alberto")

    assert use_case._read_user_context(ctx) == "no uses tablas markdown\n\ncontexto de alberto"


def test_instructions_solo_sin_archivo_per_user(use_case, users_root):
    """Si hay ``_common.md`` pero ningún archivo per-user, devuelve solo las instrucciones."""
    (users_root / "telegram").mkdir()
    (users_root / "telegram" / "_common.md").write_text("formato común del canal", encoding="utf-8")

    ctx = ChannelContext(channel_type="telegram", user_id="999", username="desconocido")

    assert use_case._read_user_context(ctx) == "formato común del canal"


def test_instructions_scopeado_por_canal(use_case, users_root):
    """``_common.md`` es por canal — telegram no hereda las de cli."""
    (users_root / "telegram").mkdir()
    (users_root / "cli").mkdir()
    (users_root / "cli" / "_common.md").write_text("instr cli", encoding="utf-8")

    ctx_telegram = ChannelContext(channel_type="telegram", user_id="1", username="alberto")

    # telegram no tiene _common.md ni archivo per-user → vacío
    assert use_case._read_user_context(ctx_telegram) == ""


@pytest.mark.parametrize(
    "malicious_username",
    ["../etc", "a/b", "a\\b", ".."],
)
def test_rechaza_separadores_de_path_en_username(use_case, users_root, malicious_username):
    """Username con ``/``, ``\\`` o ``..`` se descarta — defensa contra path traversal."""
    # Username malicioso es descartado, pero como user_id sigue siendo válido y existe
    # un archivo para él, se devuelve ESE contenido (no el del path traversal).
    (users_root / "telegram").mkdir()
    (users_root / "telegram" / "999.md").write_text("ctx por id seguro", encoding="utf-8")

    ctx = ChannelContext(channel_type="telegram", user_id="999", username=malicious_username)

    assert use_case._read_user_context(ctx) == "ctx por id seguro"


def test_rechaza_separadores_en_user_id(use_case, users_root):
    """``user_id`` con separadores también se descarta → cae a ``""``."""
    # Caso edge: user_id no debería contener separadores en la práctica
    # (canales lo sanitizan), pero defensivo: si llega así, no romper.
    ctx = ChannelContext(channel_type="cli", user_id="../etc/passwd")

    assert use_case._read_user_context(ctx) == ""
