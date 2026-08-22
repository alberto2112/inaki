"""Proveedor LLM genérico para un endpoint propio con dialecto OpenAI clásico.

Pensado para un servidor de inferencia que vos controlás —vLLM, llama.cpp
``llama-server``, un GGUF de unsloth en la red local, o cualquier API OpenAI-compat
que no tenga adapter nativo—. El nombre describe la RELACIÓN (lo configurás vos
entero) y no un vendor ni una topología: si el server se muda de tu LAN a un VPS,
el nombre sigue siendo verdad.

Se diferencia de ``openai`` en tres cosas, y cada una es un bug real si usás el
adapter equivocado:

1. **Credenciales opcionales** (``REQUIRES_CREDENTIALS = False``). Un server propio
   normalmente entra pelado. Con ``openai`` la factory te exige una ``api_key`` y
   te obliga a inventar un secreto que no existe.
2. **Sin ``Authorization`` fantasma**. La base manda ``Bearer {api_key}`` siempre;
   sin key eso viaja como el string ``"Bearer None"``. Acá el header se omite
   cuando no hay credencial, y se manda cuando sí la hay (hay servers con auth).
3. **``max_tokens``, no ``max_completion_tokens``**. ``openai`` usa la clave
   moderna; los servers OpenAI-compat hablan el dialecto clásico. Un server que no
   entiende la clave moderna, o te tira 400, o —peor— la IGNORA en silencio y
   genera hasta agotar el contexto.

``base_url`` es OBLIGATORIO: un adapter que se define por su endpoint no puede
adivinarlo. Sin default silencioso a localhost — preferimos un ``ConfigError`` que
nombra el campo antes que un connection-refused contra un puerto que nadie pidió.

**El contexto es tuyo, no del adapter**: ``llm.max_tokens`` viaja como techo de
generación y muchos servers validan ``prompt + max_tokens > n_ctx``. Heredar el
``max_tokens`` gigante de un provider cloud contra una ventana local chica es un
400 garantizado. Ajustá ``max_tokens`` y ``chat_history.max_messages`` EN EL
AGENTE que use este provider.
"""

from __future__ import annotations

from typing import ClassVar

from adapters.outbound.providers.base import ResolvedLLMConfig
from adapters.outbound.providers.openai_compatible import OpenAICompatibleProvider
from core.domain.errors import ConfigError

PROVIDER_NAME = "custom"


class CustomProvider(OpenAICompatibleProvider):
    REQUIRES_CREDENTIALS: bool = False

    _provider_label: ClassVar[str] = "Custom"
    # Inalcanzable: __init__ exige base_url antes de que la base lo consulte.
    # Se declara porque el contrato de la familia lo pide como ClassVar.
    _default_base_url: ClassVar[str] = ""

    def __init__(self, cfg: ResolvedLLMConfig) -> None:
        if not cfg.base_url:
            raise ConfigError(
                f"El provider '{cfg.provider}' (type: custom) requiere "
                f"'providers.{cfg.provider}.base_url' — por ejemplo "
                f"'http://192.168.1.50:8000/v1'. No hay endpoint por defecto: "
                f"un adapter que se define por su endpoint no puede adivinarlo."
            )
        super().__init__(cfg)

    def _build_headers(self, cfg: ResolvedLLMConfig) -> dict[str, str]:
        """``Authorization`` solo si hay credencial.

        Sin este override, un server sin auth recibe ``Authorization: Bearer None``.
        La mayoría lo ignora, pero los que validan el header rechazan el request
        con un 401 que no dice nada útil.
        """
        if not cfg.api_key:
            return {"Content-Type": "application/json"}
        return super()._build_headers(cfg)

    def _completion_params(self, *, stream: bool) -> dict:
        # Dialecto clásico: ``max_tokens``. Ver punto 3 del docstring del módulo.
        return {
            "temperature": self._cfg.temperature,
            "max_tokens": self._cfg.max_tokens,
        }
