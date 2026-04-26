"""Helper de masking para campos secret.

Vive en ``widgets/`` porque es una concern de presentación: el `Field`
guarda el valor real, y la fila / vista decide cómo mostrarlo enmascarado.
"""

from __future__ import annotations


def mask_secret(value: str) -> str:
    """Enmascara un secret para mostrar fragmentos sin exponer la credencial.

    Reglas (UX-decision#2 de la versión anterior, preservada):
      - Vacío                  → ``""`` (sin cambios)
      - Largo >= 12 chars      → ``"<prefijo5>…<sufijo4>"`` (ej: ``"sk-or…XXXX"``)
      - Largo entre 1 y 11     → ``"••••••••"`` (8 bullets fijos para no
                                  revelar nada de un secret corto)
    """
    if not value:
        return ""
    if len(value) >= 12:
        return f"{value[:5]}…{value[-4:]}"
    return "••••••••"
