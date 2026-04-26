"""
Subpantallas de edición por sección para la TUI de setup.

Cada archivo de este paquete corresponde a una sección del YAML de configuración.
La clase base ``SectionEditorScreen`` en ``_base.py`` provee la lógica genérica
de render / diff preview / guardado. Las pantallas concretas solo declaran
``SECTION_KEY``, ``TITULO`` y ``CAMPOS``.
"""
