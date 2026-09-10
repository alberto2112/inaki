"""``ApplyConfigChangesUseCase``: aplica por capa, hereda, y deshace si el validador rechaza."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from inaki.config.adapters.yaml_repository import YamlRepository
from inaki.config.ports import LayerName
from inaki.config.use_cases.apply_changes import ApplyConfigChangesUseCase, ConfigInvalidaError


@pytest.fixture
def repo(tmp_path: Path) -> YamlRepository:
    (tmp_path / "config").mkdir()
    (tmp_path / "agents").mkdir()
    (tmp_path / "config" / "global.yaml").write_text(
        "llm:\n  provider: openrouter\n  model: base\n  temperature: 0.7\n"
    )
    (tmp_path / "agents" / "dev.yaml").write_text(
        "id: dev\nname: Dev\nllm:\n  model: override\n  temperature: 0.2\n"
    )
    return YamlRepository(config_dir=tmp_path / "config", agents_dir=tmp_path / "agents")


def _leer(tmp_path: Path, rel: str) -> dict:
    return yaml.safe_load((tmp_path / rel).read_text())


def test_aplica_cambios_anidados_en_la_capa_global(tmp_path: Path, repo: YamlRepository) -> None:
    uc = ApplyConfigChangesUseCase(repo, validar=MagicMock())

    r = uc.execute(capa=LayerName.GLOBAL, cambios={"llm.model": "nuevo", "app.debug": True})

    g = _leer(tmp_path, "config/global.yaml")
    assert g["llm"] == {"provider": "openrouter", "model": "nuevo", "temperature": 0.7}
    assert g["app"] == {"debug": True}
    assert r.cambiados == ["app.debug", "llm.model"] and r.heredados == []


def test_heredar_borra_la_clave_de_la_capa_de_agente(tmp_path: Path, repo: YamlRepository) -> None:
    uc = ApplyConfigChangesUseCase(repo, validar=MagicMock())

    uc.execute(capa=LayerName.AGENT, agent_id="dev", cambios={}, heredar=["llm.temperature"])

    a = _leer(tmp_path, "agents/dev.yaml")
    assert a["llm"] == {"model": "override"}, "temperature vuelve a heredar de global"


def test_si_el_validador_rechaza_la_capa_queda_como_estaba(
    tmp_path: Path, repo: YamlRepository
) -> None:
    antes = (tmp_path / "config" / "global.yaml").read_text()
    validar = MagicMock(
        side_effect=ValueError("llm.modle: clave desconocida. ¿Quisiste decir model?")
    )
    uc = ApplyConfigChangesUseCase(repo, validar=validar)

    with pytest.raises(ConfigInvalidaError, match="clave desconocida"):
        uc.execute(capa=LayerName.GLOBAL, cambios={"llm.modle": "x"})

    validar.assert_called_once()
    assert yaml.safe_load((tmp_path / "config" / "global.yaml").read_text()) == yaml.safe_load(
        antes
    )


def test_capa_de_agente_sin_id_o_sin_cambios_es_error_de_uso(repo: YamlRepository) -> None:
    uc = ApplyConfigChangesUseCase(repo, validar=MagicMock())
    with pytest.raises(ValueError, match="requiere agent_id"):
        uc.execute(capa=LayerName.AGENT, cambios={"llm.model": "x"})
    with pytest.raises(ValueError, match="Nada que aplicar"):
        uc.execute(capa=LayerName.GLOBAL, cambios={})
