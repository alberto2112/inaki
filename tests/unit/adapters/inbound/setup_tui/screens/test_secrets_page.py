"""Tests para los helpers de SecretsPage: _mask_secret y _unflatten."""

from __future__ import annotations

from adapters.inbound.setup_tui.screens.secrets_page import _mask_secret, _unflatten


class TestMaskSecret:
    """Casos límite de _mask_secret."""

    def test_vacio_retorna_vacio(self):
        assert _mask_secret("") == ""

    def test_largo_1(self):
        """Un solo carácter → bullets fijos."""
        assert _mask_secret("x") == "••••••••"

    def test_largo_11(self):
        """11 caracteres — debajo del umbral de 12 → bullets fijos."""
        assert _mask_secret("abcdefghijk") == "••••••••"

    def test_largo_12(self):
        """12 caracteres — exactamente en el umbral → formato largo."""
        valor = "abcdefghijkl"
        resultado = _mask_secret(valor)
        # Prefijo de 5 + "…" + sufijo de 4
        assert resultado == "abcde…ijkl"

    def test_largo_largo(self):
        """Un secret largo típico (api key) → prefijo + ellipsis + sufijo."""
        valor = "sk-or-v0-abcdefghijklmnopqrstuvwxyz1234567890ABCD"
        resultado = _mask_secret(valor)
        assert resultado.startswith("sk-or")
        assert "…" in resultado
        assert resultado.endswith(valor[-4:])
        # NO debe exponer el secret completo
        assert len(resultado) < len(valor)

    def test_formato_prefijo_5_sufijo_4(self):
        """Verifica la estructura exacta: 5 chars + '…' + 4 chars."""
        valor = "123456789012345"
        resultado = _mask_secret(valor)
        prefix, _, suffix = resultado.partition("…")
        assert len(prefix) == 5
        assert len(suffix) == 4
        assert prefix == "12345"
        assert suffix == "2345"


class TestUnflatten:
    """Casos de _unflatten: clave punto-separada → dict anidado."""

    def test_clave_simple(self):
        """Una sola clave sin puntos → dict de un nivel."""
        resultado = _unflatten("api_key", "sk-x")
        assert resultado == {"api_key": "sk-x"}

    def test_clave_dos_niveles(self):
        """Dos niveles → dict anidado."""
        resultado = _unflatten("providers.openai", "val")
        assert resultado == {"providers": {"openai": "val"}}

    def test_clave_tres_niveles(self):
        """Tres niveles → dict doblemente anidado."""
        resultado = _unflatten("providers.openai.api_key", "sk-openai-xyz")
        assert resultado == {"providers": {"openai": {"api_key": "sk-openai-xyz"}}}

    def test_valor_none(self):
        """Valor None se preserva tal cual."""
        resultado = _unflatten("some.key", None)
        assert resultado == {"some": {"key": None}}

    def test_valor_int(self):
        """Tipos no-str también se preservan."""
        resultado = _unflatten("memory.keep_last_messages", 42)
        assert resultado == {"memory": {"keep_last_messages": 42}}
