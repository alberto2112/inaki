"""Descarga del modelo local de embeddings (``multilingual-e5-small`` en ONNX).

Es una capacidad del módulo embedding, no del CLI: ``inaki init`` la invoca y
le pasa un callback de progreso; acá no hay Rich ni Typer. Los ficheros son
los DOS que ``E5OnnxProvider`` espera en ``embedding.model_dirname``:
``model.onnx`` y ``tokenizer.json``.

Idempotente: un fichero ya presente no se vuelve a bajar. La descarga va a un
``.part`` y se renombra al terminar, así un corte a mitad no deja un modelo a
medias que el provider intente cargar.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

MODELO_E5_REPO = "intfloat/multilingual-e5-small"
_BASE = f"https://huggingface.co/{MODELO_E5_REPO}/resolve/main"

# (nombre local, path en el repo de HuggingFace)
FICHEROS_MODELO_E5: tuple[tuple[str, str], ...] = (
    ("model.onnx", "onnx/model.onnx"),
    ("tokenizer.json", "tokenizer.json"),
)

# Lo que informa HuggingFace hoy (fp32). Solo para mostrar antes de confirmar.
TAMANIO_APROX_MB = 470

Progreso = Callable[[str, int, int | None], None]
"""``(fichero, bytes_recibidos, bytes_totales_o_None)`` — lo llama la descarga por chunk."""


def modelo_e5_completo(dest: Path) -> bool:
    return all((dest / nombre).exists() for nombre, _ in FICHEROS_MODELO_E5)


def descargar_modelo_e5(
    dest: Path,
    *,
    progreso: Progreso | None = None,
    client: httpx.Client | None = None,
) -> list[Path]:
    """Baja a ``dest`` los ficheros que falten. Devuelve los que descargó.

    Propaga ``httpx.HTTPError`` (red, 4xx/5xx): quien llama decide qué decirle
    al operador. ``client`` es inyectable para tests.
    """
    dest.mkdir(parents=True, exist_ok=True)
    propio = client is None
    cli = client or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60.0, read=300.0))
    descargados: list[Path] = []
    try:
        for nombre, remoto in FICHEROS_MODELO_E5:
            destino = dest / nombre
            if destino.exists():
                logger.info("Modelo e5: %s ya existe, se omite", destino)
                continue
            _descargar_uno(cli, f"{_BASE}/{remoto}", destino, nombre, progreso)
            descargados.append(destino)
    finally:
        if propio:
            cli.close()
    return descargados


def _descargar_uno(
    cli: httpx.Client, url: str, destino: Path, nombre: str, progreso: Progreso | None
) -> None:
    parcial = destino.with_suffix(destino.suffix + ".part")
    with cli.stream("GET", url) as resp:
        resp.raise_for_status()
        total_hdr = resp.headers.get("content-length")
        total = int(total_hdr) if total_hdr else None
        recibidos = 0
        with parcial.open("wb") as fh:
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                fh.write(chunk)
                recibidos += len(chunk)
                if progreso is not None:
                    progreso(nombre, recibidos, total)
    parcial.replace(destino)
    logger.info("Modelo e5: descargado %s (%d bytes)", destino, recibidos)
