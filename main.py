"""Wrapper de compatibilidad para las unidades systemd generadas por el viejo ``install.sh``
(``ExecStart=... python main.py daemon``). El código real está en ``inaki/cli``.

Se conserva para que un ``git pull`` no tumbe un daemon en producción: la unidad
vieja sigue arrancando hasta que el operador corra ``inaki service install``, que
genera una unidad con el console script. Después de eso, este fichero sobra.
"""

from inaki.cli import app

if __name__ == "__main__":
    app()
