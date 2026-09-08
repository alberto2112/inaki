"""Bloque ``llm``: cerebro conversacional (provider, modelo, parámetros de muestreo).

Sección del schema de configuración. Solo declaraciones: sin I/O ni carga de YAML.
Importá desde ``inaki.config.schema`` (o ``inaki.config``).
"""

from __future__ import annotations

from pydantic import Field
from inaki.config.schema._base import _ConfigBaseModel


_LLM_TIMEOUT_FALLBACK = 60


class LLMConfig(_ConfigBaseModel):
    """Cerebro conversacional del agente: qué modelo responde y con qué parámetros.

    Bloque per-agente: se declara en ``global.yaml`` como default de todos y se
    pisa campo a campo en ``agents/{id}.yaml``. NO lleva credenciales — ``provider``
    referencia una entrada del registry ``providers:``, de donde salen ``api_key``
    y ``base_url``.

    Los jobs de memoria pueden usar otro modelo sin tocar este bloque: ver
    ``memories.llm`` (``MemoryLLMConfig``), que lo hereda y lo override campo a campo.
    """

    provider: str = "openrouter"
    """KEY del registry ``providers:`` que aporta las credenciales y el endpoint.

    Adapters incluidos: ``openrouter``, ``openai``, ``openai_responses``,
    ``anthropic``, ``deepseek``, ``groq``, ``ollama``, ``custom``. Se
    auto-descubren por la constante ``PROVIDER_NAME`` del módulo, así que agregar
    uno nuevo es crear ``adapters/outbound/providers/{name}.py`` y declarar
    ``providers.{name}``."""

    model: str = "anthropic/claude-3-5-haiku"
    """Identificador del modelo, en el formato que espera el provider elegido.

    OpenRouter exige el prefijo del vendor (``anthropic/claude-3-5-haiku``); el
    adapter ``anthropic`` nativo NO lo lleva (``claude-sonnet-4-5``). Ollama usa
    el nombre local del modelo (``llama3.2``)."""

    temperature: float = 0.7
    """Aleatoriedad del muestreo: ``0.0`` determinista, valores altos más creativo.

    Orientativo: 0.3-0.5 para tareas de código o extracción, 0.7-0.9 para
    conversación. El adapter ``openai_responses`` la OMITE cuando
    ``reasoning_effort`` está seteado (la API devuelve 400 si van juntas)."""

    max_tokens: int = 2048
    """Techo de tokens que el modelo puede GENERAR en una respuesta.

    No acota el prompt de entrada. Subirlo para respuestas largas (código,
    análisis). Cuidado con servidores locales OpenAI-compat: muchos validan
    ``prompt + max_tokens > n_ctx`` y devuelven HTTP 400 — contra una ventana
    chica hay que bajarlo junto con ``chat_history.max_messages``, y hacerlo en
    el AGENTE que usa ese provider, no en el global."""

    reasoning_effort: str | None = None
    """Intensidad del modo razonamiento (thinking). ``null`` = desactivado.

    Cada adapter lo traduce a su dialecto: Groq lo manda tal cual y cambia
    ``max_tokens`` por ``max_completion_tokens``; ``anthropic`` lo convierte en
    el ``budget_tokens`` del extended thinking; ``openai_responses`` lo manda
    como ``reasoning.effort`` y omite ``temperature``. Con thinking activo
    conviene subir ``timeout_seconds``, y es el flag del que depende
    ``channels.thinking_indicator``."""

    timeout_seconds: int = Field(default=_LLM_TIMEOUT_FALLBACK, gt=0)
    """Timeout HTTP del request al provider, en segundos.

    Default ``60``. Recomendado subirlo (180-300) cuando se usa thinking mode
    sobre queries complejas, donde el modelo puede tardar mucho más en
    responder.

    Un valor no parseable o ``<= 0`` es un error de config, no algo que
    sanitizar: hasta la Fase 4 del refactor esto caía al fallback de 60s en
    silencio, así que ``timeout_seconds: "sesenta"`` corría con 60 y el
    operador creía haber configurado otra cosa.
    """

    request_delay_seconds: float = Field(default=2.0, ge=0)
    """Espera mínima (segundos) ANTES de cada llamada al provider dentro del
    loop agéntico, EXCEPTO la primera del turno.

    Default ``2.0``. Evita saturar el rate limiter del provider cuando el modelo
    encadena varias tool calls en un mismo turno (cada iteración del loop es un
    ``llm.complete()``): sin throttle, 5 tool calls disparan 5 requests
    back-to-back. La primera llamada del turno NO se demora (sería latencia pura
    sin proteger nada — el rate limiter se satura por las llamadas encadenadas).

    ``0`` desactiva el throttle. Un negativo o un valor no parseable es un
    error de config (antes se clampeaban o caían al default en silencio).
    """
