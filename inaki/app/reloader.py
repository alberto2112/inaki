"""
DaemonReloader — coordina reinicios in-place del proceso del daemon.

NO es un puerto del dominio: reload no es un concepto de negocio, es un concern
del proceso. Vive en infraestructura y lo consumen los inbound adapters
(admin REST, Telegram bot) para señalar al runner que debe cerrar todos los
channels, recargar config y volver a levantar todo.

El runner expone `wait_for_reload()` en su `asyncio.wait(..., FIRST_COMPLETED)`
junto con las tasks de channels y la señal de shutdown — cuando alguien llama
`request_reload()`, el runner cancela las tasks, ejecuta `app_container.shutdown()`,
re-bootstrappea config y vuelve a levantar el ciclo.
"""

from __future__ import annotations

import asyncio

from inaki.config.ports import ModoReload
from inaki.config.runtime_overrides import OverridesDeRuntime


class DaemonReloader:
    """Señal de reload con dos modos (feature ``config-set-efimero``).

    - ``request_reload()`` — MANUAL (las tres puertas: ``POST /admin/reload``,
      ``inaki reload`` y ``/reload`` de Telegram): descarta los overrides de runtime
      EN EL MOMENTO del pedido y devuelve una línea por cada uno descartado. Descartar
      acá hace imposible que un ``config set`` posterior sobreviva: el use case ve
      ``modo == "descartar"`` y rechaza.
    - ``request_reload_para_aplicar()`` — solo ``config set``: el store se conserva y
      el bootstrap lo lee. Si ya hay un manual pendiente no lo pisa (gana el manual).

    ``overrides`` es opcional para que quien construye el reloader pelado (tests) no
    tenga que armar un store.
    """

    def __init__(self, overrides: OverridesDeRuntime | None = None) -> None:
        self._event = asyncio.Event()
        self._overrides = overrides
        self._modo: ModoReload | None = None

    @property
    def modo(self) -> ModoReload | None:
        return self._modo

    def request_reload(self) -> list[str]:
        descartados = self._overrides.descartar_todo() if self._overrides is not None else []
        self._modo = "descartar"
        self._event.set()
        return descartados

    def request_reload_para_aplicar(self) -> None:
        if self._modo is None:
            self._modo = "aplicar"
        self._event.set()

    async def wait_for_reload(self) -> None:
        await self._event.wait()

    def was_triggered(self) -> bool:
        return self._event.is_set()
