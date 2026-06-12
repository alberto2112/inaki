"""Tests del YamlToolConfigStore (Tool Config Protocol)."""

from __future__ import annotations

import stat
from pathlib import Path

from ruamel.yaml import YAML

from adapters.outbound.config_repository.yaml_tool_config_store import YamlToolConfigStore


def _make_store(tmp_path: Path, initial: dict | None = None) -> YamlToolConfigStore:
    return YamlToolConfigStore(
        secrets_path=tmp_path / "global.secrets.yaml",
        key_path=tmp_path / "secret.key",
        initial=initial,
    )


def _leer_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return YAML().load(f) or {}


def test_roundtrip_campo_sensible_cifrado_en_reposo(tmp_path: Path):
    store = _make_store(tmp_path)

    store.set("web_search", {"api_key": "tvly-secreta"}, sensitive=frozenset({"api_key"}))

    # get() devuelve el valor en claro
    assert store.get("web_search")["api_key"] == "tvly-secreta"
    # pero en el YAML persiste cifrado con prefijo enc:
    en_disco = _leer_yaml(tmp_path / "global.secrets.yaml")
    assert en_disco["tool_config"]["web_search"]["api_key"].startswith("enc:")


def test_campos_no_sensibles_quedan_en_plano(tmp_path: Path):
    store = _make_store(tmp_path)

    store.set("web_search", {"max_results": 7, "search_depth": "advanced"})

    en_disco = _leer_yaml(tmp_path / "global.secrets.yaml")
    assert en_disco["tool_config"]["web_search"]["max_results"] == 7
    assert en_disco["tool_config"]["web_search"]["search_depth"] == "advanced"


def test_masked_enmascara_solo_los_cifrados(tmp_path: Path):
    store = _make_store(tmp_path)
    store.set(
        "exchange",
        {"username": "alberto", "password": "secreta"},
        sensitive=frozenset({"password"}),
    )

    masked = store.masked("exchange")

    assert masked["username"] == "alberto"
    assert masked["password"] == "***"


def test_set_mergea_y_omite_vacios(tmp_path: Path):
    store = _make_store(tmp_path)
    store.set("web_search", {"api_key": "k1", "max_results": 5}, sensitive=frozenset({"api_key"}))

    # None y "" no pisan lo existente; campos nuevos se agregan
    store.set("web_search", {"api_key": "", "search_depth": "basic", "max_results": None})

    config = store.get("web_search")
    assert config["api_key"] == "k1"
    assert config["max_results"] == 5
    assert config["search_depth"] == "basic"


def test_namespaces_aislados(tmp_path: Path):
    store = _make_store(tmp_path)
    store.set("web_search", {"api_key": "k1"}, sensitive=frozenset({"api_key"}))
    store.set("exchange", {"username": "alberto"})

    assert "username" not in store.get("web_search")
    assert "api_key" not in store.get("exchange")
    assert store.get("inexistente") == {}


def test_clave_autogenerada_con_permisos_0600(tmp_path: Path):
    store = _make_store(tmp_path)

    store.set("ns", {"token": "x"}, sensitive=frozenset({"token"}))

    key_path = tmp_path / "secret.key"
    assert key_path.exists()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_persistencia_preserva_resto_del_secrets_yaml(tmp_path: Path):
    secrets = tmp_path / "global.secrets.yaml"
    secrets.write_text(
        "# credenciales de providers\nproviders:\n  openai:\n    api_key: sk-123\n",
        encoding="utf-8",
    )
    store = _make_store(tmp_path)

    store.set("web_search", {"max_results": 3})

    contenido = secrets.read_text(encoding="utf-8")
    en_disco = _leer_yaml(secrets)
    assert en_disco["providers"]["openai"]["api_key"] == "sk-123"
    assert en_disco["tool_config"]["web_search"]["max_results"] == 3
    # ruamel preserva el comentario existente
    assert "# credenciales de providers" in contenido


def test_initial_cifrado_se_descifra_con_la_misma_clave(tmp_path: Path):
    """Simula el reinicio del daemon: la config mergeada llega como initial."""
    store1 = _make_store(tmp_path)
    store1.set("web_search", {"api_key": "tvly-abc"}, sensitive=frozenset({"api_key"}))
    en_disco = _leer_yaml(tmp_path / "global.secrets.yaml")

    store2 = _make_store(tmp_path, initial=dict(en_disco["tool_config"]))

    assert store2.get("web_search")["api_key"] == "tvly-abc"


def test_clave_rotada_omite_campo_con_warning(tmp_path: Path):
    """Si secret.key cambia, los enc: viejos se omiten en vez de romper."""
    store1 = _make_store(tmp_path)
    store1.set(
        "web_search",
        {"api_key": "tvly-abc", "max_results": 5},
        sensitive=frozenset({"api_key"}),
    )
    en_disco = _leer_yaml(tmp_path / "global.secrets.yaml")

    (tmp_path / "secret.key").unlink()  # clave perdida/rotada
    store2 = _make_store(tmp_path, initial=dict(en_disco["tool_config"]))

    config = store2.get("web_search")
    assert "api_key" not in config  # omitido, no explota
    assert config["max_results"] == 5  # los planos sobreviven
