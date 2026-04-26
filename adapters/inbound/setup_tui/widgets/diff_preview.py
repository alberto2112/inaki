"""
DiffPreview — widget que renderiza un diff YAML entre config en disco y cambios pendientes.

Usa ``difflib.unified_diff`` para calcular las líneas de diff.
Las líneas con ``+`` se colorean en verde, las con ``-`` en rojo.
Si no hay diferencias, muestra un mensaje neutro.

Uso típico en pantallas:
    diff = DiffPreview()
    diff.actualizar(yaml_actual, yaml_nuevo)
"""

from __future__ import annotations

import difflib

from textual.app import ComposeResult
from textual.widgets import Static


def calcular_diff(antes: str, despues: str, etiqueta: str = "config") -> str:
    """
    Calcula el diff unificado entre ``antes`` y ``despues``.

    Args:
        antes: Contenido YAML actual en disco (string).
        despues: Contenido YAML con los cambios pendientes (string).
        etiqueta: Label que aparece en el encabezado del diff.

    Returns:
        String multi-línea con el diff. Vacío si no hay diferencias.
    """
    lineas_antes = antes.splitlines(keepends=True)
    lineas_despues = despues.splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            lineas_antes,
            lineas_despues,
            fromfile=f"{etiqueta} (disco)",
            tofile=f"{etiqueta} (pendiente)",
            lineterm="",
        )
    )
    return "".join(diff)


def _colorear_diff(diff_texto: str) -> str:
    """
    Agrega marcado Rich para colorear el diff.

    Líneas ``+``: verde. Líneas ``-``: rojo. Encabezados: azul.
    """
    lineas_coloreadas = []
    for linea in diff_texto.splitlines():
        if linea.startswith("+++") or linea.startswith("---"):
            lineas_coloreadas.append(f"[blue]{linea}[/blue]")
        elif linea.startswith("+"):
            lineas_coloreadas.append(f"[green]{linea}[/green]")
        elif linea.startswith("-"):
            lineas_coloreadas.append(f"[red]{linea}[/red]")
        elif linea.startswith("@@"):
            lineas_coloreadas.append(f"[cyan]{linea}[/cyan]")
        else:
            lineas_coloreadas.append(linea)
    return "\n".join(lineas_coloreadas)


class DiffPreview(Static):
    """
    Widget que muestra el diff YAML entre la versión en disco y los cambios pendientes.

    Actualiza su contenido via ``actualizar(antes, despues)``.
    """

    DEFAULT_CSS = """
    DiffPreview {
        height: auto;
        max-height: 20;
        overflow-y: auto;
        border: solid $primary-darken-2;
        padding: 0 1;
        background: $surface;
    }
    """

    _SIN_CAMBIOS = "[dim]Sin cambios pendientes.[/dim]"
    _SIN_DATOS = "[dim]Cargando diff...[/dim]"

    def __init__(self, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(self._SIN_DATOS, id=id, classes=classes, markup=True)
        self._antes: str = ""
        self._despues: str = ""

    def compose(self) -> ComposeResult:
        # Widget hoja — nada que componer
        return iter([])

    def actualizar(self, antes: str, despues: str, etiqueta: str = "config") -> None:
        """
        Recalcula y muestra el diff entre ``antes`` (disco) y ``despues`` (pendiente).

        Args:
            antes: YAML actual en disco.
            despues: YAML con cambios pendientes.
            etiqueta: Label para el encabezado del diff.
        """
        self._antes = antes
        self._despues = despues
        diff_texto = calcular_diff(antes, despues, etiqueta)
        if not diff_texto:
            self.update(self._SIN_CAMBIOS)
        else:
            self.update(_colorear_diff(diff_texto))

    @property
    def tiene_cambios(self) -> bool:
        """Retorna True si hay diferencias entre antes y después."""
        return self._antes != self._despues
