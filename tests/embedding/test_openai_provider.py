"""``OpenAIEmbeddingProvider``: tope de entrada, red de seguridad ante 400 y errores legibles.

Caso real (bg-1001): un texto de 60.388 chars llegó sin tocar a ``text-embedding-3-small``
(máx. 8192 tokens) y OpenAI contestó 400. El error logueado no incluía el cuerpo de la
respuesta, así que la causa no se podía diagnosticar desde el log.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx

from inaki.embedding.base import ResolvedEmbeddingConfig
from inaki.embedding.openai import MAX_CHARS_ENTRADA, OpenAIEmbeddingProvider
from inaki.shared.errors import EmbeddingError

ENDPOINT = "https://api.openai.com/v1/embeddings"
_VEC = [0.5] * 384
_OK = {"data": [{"embedding": _VEC}]}
_API_KEY = "sk-secreta-que-no-debe-aparecer"


def _provider() -> OpenAIEmbeddingProvider:
    return OpenAIEmbeddingProvider(
        ResolvedEmbeddingConfig(
            provider="openai",
            model_dirname="models/x",
            model="text-embedding-3-small",
            dimension=384,
            cache_filename="cache.db",
            api_key=_API_KEY,
        )
    )


def _error_contexto(codigo: str | None = "context_length_exceeded") -> httpx.Response:
    return httpx.Response(
        400,
        json={
            "error": {
                "message": (
                    "This model's maximum context length is 8192 tokens, however you "
                    "requested 15000 tokens. Please reduce your prompt."
                ),
                "type": "invalid_request_error",
                "param": None,
                "code": codigo,
            }
        },
    )


def _enviado(route: respx.Route, indice: int) -> str:
    return json.loads(route.calls[indice].request.content)["input"]


@respx.mock
async def test_texto_corto_viaja_intacto() -> None:
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_OK))

    vec = await _provider().embed_query("hola mundo")

    assert vec == _VEC
    assert _enviado(route, 0) == "hola mundo"


@respx.mock
async def test_texto_enorme_se_recorta_conservando_la_cabeza(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="inaki.embedding.openai")
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_OK))
    texto = "CABEZA" + "x" * 60_000 + "COLA"

    await _provider().embed_query(texto)

    enviado = _enviado(route, 0)
    assert len(enviado) == MAX_CHARS_ENTRADA
    assert enviado.startswith("CABEZA")
    assert "COLA" not in enviado
    assert route.call_count == 1


@respx.mock
async def test_recorte_de_query_es_debug(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="inaki.embedding.openai")
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_OK))

    await _provider().embed_query("x" * (MAX_CHARS_ENTRADA + 10))

    recortes = [r for r in caplog.records if "recort" in r.getMessage().lower()]
    assert recortes and all(r.levelno == logging.DEBUG for r in recortes)


@respx.mock
async def test_recorte_de_passage_es_warning_con_largos(caplog) -> None:
    """Un passage se indexa: recortarlo pierde contenido guardado → WARNING."""
    caplog.set_level(logging.DEBUG, logger="inaki.embedding.openai")
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_OK))
    largo = MAX_CHARS_ENTRADA + 1234

    await _provider().embed_passage("x" * largo)

    avisos = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(avisos) == 1
    assert str(largo) in avisos[0].getMessage()
    assert str(MAX_CHARS_ENTRADA) in avisos[0].getMessage()


@respx.mock
async def test_400_de_contexto_reintenta_con_la_mitad() -> None:
    route = respx.post(ENDPOINT).mock(
        side_effect=[_error_contexto(), httpx.Response(200, json=_OK)]
    )
    texto = "y" * 20_000

    vec = await _provider().embed_query(texto)

    assert vec == _VEC
    assert route.call_count == 2
    assert len(_enviado(route, 1)) == 10_000


@respx.mock
async def test_400_de_contexto_se_detecta_por_el_mensaje_sin_code() -> None:
    """Defensivo: si OpenAI no manda ``code``, el mensaje alcanza."""
    route = respx.post(ENDPOINT).mock(
        side_effect=[_error_contexto(codigo=None), httpx.Response(200, json=_OK)]
    )

    await _provider().embed_query("y" * 100)

    assert route.call_count == 2


@respx.mock
async def test_400_de_contexto_reintenta_como_maximo_dos_veces() -> None:
    route = respx.post(ENDPOINT).mock(return_value=_error_contexto())

    with pytest.raises(EmbeddingError):
        await _provider().embed_query("y" * 20_000)

    assert route.call_count == 3  # original + 2 recortes a la mitad
    assert [len(_enviado(route, i)) for i in range(3)] == [20_000, 10_000, 5_000]


@respx.mock
async def test_otro_400_falla_de_inmediato_con_el_cuerpo_en_el_mensaje() -> None:
    cuerpo = {"error": {"message": "Invalid value for 'dimensions'", "code": "bad_dims"}}
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(400, json=cuerpo))

    with pytest.raises(EmbeddingError) as info:
        await _provider().embed_query("hola")

    assert route.call_count == 1
    mensaje = str(info.value)
    assert "400" in mensaje
    assert "Invalid value for 'dimensions'" in mensaje


@respx.mock
async def test_el_cuerpo_del_error_va_truncado_y_sin_credenciales() -> None:
    respx.post(ENDPOINT).mock(return_value=httpx.Response(401, text="Z" * 5000))

    with pytest.raises(EmbeddingError) as info:
        await _provider().embed_query("hola")

    mensaje = str(info.value)
    assert "Z" * 300 in mensaje
    assert "Z" * 1000 not in mensaje
    assert _API_KEY not in mensaje
    assert "Bearer" not in mensaje


@respx.mock
async def test_429_sigue_reintentando_como_antes(monkeypatch) -> None:
    async def sin_espera(_seg: float) -> None:
        return None

    monkeypatch.setattr("inaki.embedding.openai.asyncio.sleep", sin_espera)
    route = respx.post(ENDPOINT).mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=_OK)]
    )

    assert await _provider().embed_query("hola") == _VEC
    assert route.call_count == 2
