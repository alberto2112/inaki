"""Tests para ``ensure_user_channel_dirs()``.

Garantiza que cada canal configurado en cualquier agente tenga su
subdirectorio ``~/.inaki/users/{channel}/`` listo para que el operador
deposite archivos per-user (ver ``RunAgentUseCase._read_user_context``).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from infrastructure.config import ensure_user_channel_dirs


def _agent_cfg(agent_id: str, channels: dict[str, dict]) -> SimpleNamespace:
    """Construye un stub mínimo con ``id`` y ``channels`` — único API que la
    función toca. Evita la fricción de armar un ``AgentConfig`` completo."""
    return SimpleNamespace(id=agent_id, channels=channels)


def test_crea_un_dir_por_canal(tmp_path: Path) -> None:
    """Por cada key en ``channels`` aparece ``users/{key}/``."""
    cfgs = [_agent_cfg("dev", {"telegram": {}, "cli": {}})]

    ensure_user_channel_dirs(tmp_path, cfgs)

    assert (tmp_path / "users" / "telegram").is_dir()
    assert (tmp_path / "users" / "cli").is_dir()


def test_deduplica_canales_entre_agentes(tmp_path: Path) -> None:
    """Si varios agentes declaran el mismo canal, se crea UN solo dir (set)."""
    cfgs = [
        _agent_cfg("dev", {"telegram": {}}),
        _agent_cfg("prod", {"telegram": {}, "rest": {}}),
    ]

    ensure_user_channel_dirs(tmp_path, cfgs)

    assert sorted(p.name for p in (tmp_path / "users").iterdir()) == [
        "rest",
        "telegram",
    ]


def test_es_idempotente(tmp_path: Path) -> None:
    """Llamadas repetidas no fallan ni borran archivos previos del operador."""
    cfgs = [_agent_cfg("dev", {"telegram": {}})]
    ensure_user_channel_dirs(tmp_path, cfgs)

    # Operador coloca un archivo per-user
    user_file = tmp_path / "users" / "telegram" / "alberto.md"
    user_file.write_text("contexto previo", encoding="utf-8")

    ensure_user_channel_dirs(tmp_path, cfgs)

    assert user_file.read_text(encoding="utf-8") == "contexto previo"


def test_sin_canales_no_crea_subdirs(tmp_path: Path) -> None:
    """Agente sin ``channels`` configurado → no aparecen subdirs (root tampoco)."""
    cfgs = [_agent_cfg("dev", {})]
    ensure_user_channel_dirs(tmp_path, cfgs)

    users_root = tmp_path / "users"
    # Root no se crea sin canales — esto es ok: no hay nada que descubrir.
    assert not users_root.exists() or list(users_root.iterdir()) == []


def test_iterable_vacio(tmp_path: Path) -> None:
    """Lista de agentes vacía → no-op, sin errores."""
    ensure_user_channel_dirs(tmp_path, [])
    assert not (tmp_path / "users").exists()


def test_no_falla_si_no_puede_crear(tmp_path: Path, monkeypatch, caplog) -> None:
    """Error de OS al crear un dir → log warning, no aborta el arranque.

    El resto de canales del listado SÍ deben crearse (un canal roto no debe
    bloquear a los demás).
    """
    import infrastructure.config as cfg_mod

    real_mkdir = Path.mkdir

    def selective_mkdir(self, *args, **kwargs):
        if self.name == "telegram":
            raise PermissionError("simulated")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", selective_mkdir)

    cfgs = [_agent_cfg("dev", {"telegram": {}, "cli": {}})]

    with caplog.at_level("WARNING", logger=cfg_mod.__name__):
        ensure_user_channel_dirs(tmp_path, cfgs)

    # cli/ se creó pese al fallo de telegram/
    assert (tmp_path / "users" / "cli").is_dir()
    assert not (tmp_path / "users" / "telegram").exists()
    assert any("No se pudo crear" in rec.message for rec in caplog.records)
