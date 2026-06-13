"""Tests del YamlToolConfigStore (Tool Config Protocol)."""

from __future__ import annotations

import stat
from pathlib import Path

from ruamel.yaml import YAML

from adapters.outbound.config_repository.yaml_tool_config_store import YamlToolConfigStore


def _make_store(tmp_path: Path) -> YamlToolConfigStore:
    """El store es dueño de su propio ``tool_config.yaml``; lo lee al construirse."""
    return YamlToolConfigStore(
        store_path=tmp_path / "tool_config.yaml",
        key_path=tmp_path / "secret.key",
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
    en_disco = _leer_yaml(tmp_path / "tool_config.yaml")
    assert en_disco["tool_config"]["web_search"]["api_key"].startswith("enc:")


def test_campos_no_sensibles_quedan_en_plano(tmp_path: Path):
    store = _make_store(tmp_path)

    store.set("web_search", {"max_results": 7, "search_depth": "advanced"})

    en_disco = _leer_yaml(tmp_path / "tool_config.yaml")
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


def test_archivo_propio_con_permisos_0600(tmp_path: Path):
    """El tool_config.yaml tiene enc: adentro → debe quedar 0600."""
    store = _make_store(tmp_path)

    store.set("web_search", {"api_key": "k"}, sensitive=frozenset({"api_key"}))

    store_path = tmp_path / "tool_config.yaml"
    assert store_path.exists()
    assert stat.S_IMODE(store_path.stat().st_mode) == 0o600


def test_persistir_preserva_comentarios_del_archivo(tmp_path: Path):
    """ruamel preserva comentarios/formato del tool_config.yaml entre escrituras."""
    store_path = tmp_path / "tool_config.yaml"
    store_path.write_text(
        "# config de tools — gestionada por el daemon\ntool_config:\n"
        "  web_search:\n    max_results: 3\n",
        encoding="utf-8",
    )
    store = _make_store(tmp_path)  # lee el archivo existente

    store.set("exchange", {"username": "alberto"})

    contenido = store_path.read_text(encoding="utf-8")
    en_disco = _leer_yaml(store_path)
    # lo preexistente sobrevive, lo nuevo se agrega
    assert en_disco["tool_config"]["web_search"]["max_results"] == 3
    assert en_disco["tool_config"]["exchange"]["username"] == "alberto"
    assert "# config de tools" in contenido


def test_sobrevive_al_reinicio_leyendo_su_propio_archivo(tmp_path: Path):
    """El bug de producción: la config debe sobrevivir al reinicio del daemon.

    Antes el store se sembraba de ``global_config.tool_config`` (que el loader
    descartaba) → tras reiniciar quedaba vacío. Ahora lee su propio archivo, así
    que una instancia nueva (= proceso nuevo) recupera lo persistido del disco.
    """
    store1 = _make_store(tmp_path)
    store1.set("web_search", {"api_key": "tvly-abc"}, sensitive=frozenset({"api_key"}))
    store1.set("exchange", {"username": "alberto", "ews_url": "https://x/EWS"})

    # Simula el reinicio: instancia nueva, mismo archivo en disco.
    store2 = _make_store(tmp_path)

    assert store2.get("web_search")["api_key"] == "tvly-abc"
    assert store2.get("exchange")["username"] == "alberto"
    assert store2.get("exchange")["ews_url"] == "https://x/EWS"


def test_update_parcial_preserva_resto_tras_reinicio(tmp_path: Path):
    """Requisito de aliases: un set parcial (ej. add_alias) NO debe perjudicar
    otras configuraciones — ni del mismo namespace ni de otras tools."""
    store1 = _make_store(tmp_path)
    store1.set(
        "exchange",
        {"username": "alberto", "password": "secreta"},
        sensitive=frozenset({"password"}),
    )
    store1.set("web_search", {"api_key": "k1"}, sensitive=frozenset({"api_key"}))

    # Reinicio + update parcial de exchange (como hace add_alias con calendars)
    store2 = _make_store(tmp_path)
    store2.set("exchange", {"calendars": [{"aliases": ["a"], "email": "x@y"}]})

    exchange = store2.get("exchange")
    assert exchange["calendars"] == [{"aliases": ["a"], "email": "x@y"}]
    assert exchange["username"] == "alberto"  # no se pisó
    assert exchange["password"] == "secreta"  # sigue descifrándose
    assert store2.get("web_search")["api_key"] == "k1"  # otra tool intacta


def test_clave_rotada_omite_campo_con_warning(tmp_path: Path):
    """Si secret.key cambia, los enc: viejos se omiten en vez de romper."""
    store1 = _make_store(tmp_path)
    store1.set(
        "web_search",
        {"api_key": "tvly-abc", "max_results": 5},
        sensitive=frozenset({"api_key"}),
    )

    (tmp_path / "secret.key").unlink()  # clave perdida/rotada
    store2 = _make_store(tmp_path)  # relee el archivo con la clave ausente/nueva

    config = store2.get("web_search")
    assert "api_key" not in config  # omitido, no explota
    assert config["max_results"] == 5  # los planos sobreviven
