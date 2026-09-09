"""
Chunker de documentos para DocumentKnowledgeSource.

Estrategia:
- Markdown (.md): split en headers (#, ##, ###) y luego ventana deslizante dentro de cada sección.
- PDF (.pdf): extrae texto por página con pypdf.PdfReader y aplica ventana deslizante sobre el total.
- Cualquier otro archivo (txt, etc.): ventana deslizante pura.

Retorna una lista de strings (chunks).
"""

from __future__ import annotations

import re
from pathlib import Path


def _ventana_deslizante(palabras: list[str], chunk_size: int, chunk_overlap: int) -> list[str]:
    """Genera chunks de `chunk_size` palabras con solapamiento de `chunk_overlap`."""
    if not palabras:
        return []

    stride = max(1, chunk_size - chunk_overlap)
    chunks: list[str] = []
    inicio = 0

    while inicio < len(palabras):
        fin = inicio + chunk_size
        chunk = " ".join(palabras[inicio:fin])
        if chunk.strip():
            chunks.append(chunk)
        inicio += stride

    return chunks


def _extraer_secciones_markdown(texto: str) -> list[str]:
    """
    Divide un texto Markdown en secciones por headers (# / ## / ###).
    El texto antes del primer header se trata como sección anónima.
    """
    patron_header = re.compile(r"^#{1,3} .+", re.MULTILINE)
    secciones: list[str] = []

    posiciones = [m.start() for m in patron_header.finditer(texto)]

    if not posiciones:
        return [texto]

    # Texto antes del primer header
    if posiciones[0] > 0:
        pre = texto[: posiciones[0]].strip()
        if pre:
            secciones.append(pre)

    for i, inicio in enumerate(posiciones):
        fin = posiciones[i + 1] if i + 1 < len(posiciones) else len(texto)
        seccion = texto[inicio:fin].strip()
        if seccion:
            secciones.append(seccion)

    return secciones


def _extraer_texto_pdf(path: Path) -> str:
    """
    Extrae el texto completo de un PDF usando pypdf.PdfReader.
    Las páginas se concatenan con un salto de línea.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ImportError(
            "pypdf es requerido para procesar archivos PDF. Instalá con: pip install pypdf"
        ) from exc

    reader = PdfReader(str(path))
    paginas: list[str] = []
    for pagina in reader.pages:
        texto = pagina.extract_text() or ""
        if texto.strip():
            paginas.append(texto)

    return "\n".join(paginas)


def chunkear_archivo(
    path: Path,
    chunk_size: int = 500,
    chunk_overlap: int = 80,
) -> list[str]:
    """
    Lee un archivo y lo divide en chunks de `chunk_size` palabras con `chunk_overlap` de solapamiento.

    Args:
        path: Ruta al archivo a procesar.
        chunk_size: Tamaño de cada chunk en palabras.
        chunk_overlap: Número de palabras de solapamiento entre chunks consecutivos.

    Returns:
        Lista de strings (chunks). Vacío si el archivo no tiene contenido útil.

    Raises:
        OSError: Si el archivo no se puede leer.
        ImportError: Si se necesita pypdf y no está instalado.
    """
    sufijo = path.suffix.lower()

    if sufijo == ".pdf":
        texto = _extraer_texto_pdf(path)
        palabras = texto.split()
        return _ventana_deslizante(palabras, chunk_size, chunk_overlap)

    # Markdown y texto plano
    try:
        texto = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raise

    if not texto.strip():
        return []

    if sufijo == ".md":
        secciones = _extraer_secciones_markdown(texto)
        chunks: list[str] = []
        for seccion in secciones:
            palabras = seccion.split()
            chunks.extend(_ventana_deslizante(palabras, chunk_size, chunk_overlap))
        return chunks

    # Texto plano y cualquier otro formato
    palabras = texto.split()
    return _ventana_deslizante(palabras, chunk_size, chunk_overlap)
