"""Guard de ``mask_secret`` — el enmascarado de credenciales en la TUI.

Estos tests vivían en ``screens/test_secrets_page.py`` y murieron con la
página al erradicar la capa de secrets. El helper sigue vivo (lo consumen
``config_row.py`` y ``detail_row.py``): una regresión en el corte
prefijo/sufijo expondría más caracteres de una key sin que nada fallara.
"""

from __future__ import annotations

from adapters.inbound.setup_tui.widgets._masking import mask_secret


def test_vacio_retorna_vacio() -> None:
    assert mask_secret("") == ""


def test_secret_corto_no_revela_nada() -> None:
    """1..11 chars → bullets fijos: ni el largo real se filtra."""
    for valor in ("x", "abcdefghijk"):
        assert mask_secret(valor) == "••••••••"


def test_umbral_exacto_de_12_usa_formato_largo() -> None:
    assert mask_secret("abcdefghijkl") == "abcde…ijkl"


def test_formato_prefijo_5_sufijo_4() -> None:
    resultado = mask_secret("123456789012345")
    prefix, _, suffix = resultado.partition("…")
    assert (len(prefix), len(suffix)) == (5, 4)


def test_api_key_tipica() -> None:
    valor = "sk-or-v0-abcdefghijklmnopqrstuvwxyz1234567890ABCD"
    resultado = mask_secret(valor)
    assert resultado.startswith("sk-or") and resultado.endswith(valor[-4:]) and "…" in resultado
