"""DTO estructurada devuelta por ``ILLMProvider.complete()``.

Reemplaza al antiguo contrato de devolver ``str`` (donde los tool_calls se
serializaban a JSON dentro del string). Ahora el adapter expone por separado
los text blocks y los tool_calls que vienen juntos en una misma respuesta
del LLM. Esto permite al tool loop emitir los text blocks al canal inbound
(mensajes intermedios tipo "ok, voy a buscar esto...") ANTES de ejecutar
las tools de la misma iteración.

- ``text_blocks``: lista de bloques de texto del assistant, en orden.
  Vacía si la respuesta fue solo tool_calls.
- ``tool_calls``: lista de tool_calls en formato nativo del provider
  (OpenAI-compatible dict con ``id``, ``function.name``, ``function.arguments``).
  Vacía si la respuesta fue solo texto.
- ``raw``: string crudo de debug/logging — representación textual best-effort
  de la respuesta original del provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LLMResponse:
    text_blocks: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    raw: str = ""

    @property
    def text(self) -> str:
        """Concatenación de los text_blocks con saltos de línea.

        Útil para consumidores que no se preocupan por los límites entre
        bloques (por ejemplo el extractor de memoria).
        """
        return "\n".join(self.text_blocks)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    @classmethod
    def of_text(cls, text: str) -> LLMResponse:
        """Helper para respuestas text-only (tests, Ollama, etc.)."""
        return cls(
            text_blocks=[text] if text else [],
            tool_calls=[],
            raw=text,
        )
