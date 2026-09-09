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


class DaemonReloader:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    def request_reload(self) -> None:
        self._event.set()

    async def wait_for_reload(self) -> None:
        await self._event.wait()

    def was_triggered(self) -> bool:
        return self._event.is_set()
