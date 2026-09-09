from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from contextlib import suppress
from typing import TYPE_CHECKING, Protocol

import httpx


if TYPE_CHECKING:
    from inaki.scheduler.domain.task import ShellExecPayload, WebhookPayload


class _Ejecutable(Protocol):
    """Un use case con ``execute()`` — el scheduler no conoce los de memoria, solo los dispara."""

    async def execute(self) -> str: ...


class ConsolidationDispatchAdapter:
    """Thin wrapper so the scheduler service doesn't import the use case directly."""

    def __init__(self, use_case: _Ejecutable) -> None:
        self._uc = use_case

    async def consolidate_all(self) -> str:
        return await self._uc.execute()


class ReconcileDispatchAdapter:
    """Thin wrapper que expone ``reconcile(agent_id)`` al ``SchedulerService``.

    Resuelve la instancia de ``ReconcileMemoryUseCase`` del agente en runtime
    desde el dict de use cases que arma el ensamblador. Si el agente
    no existe o no tiene reconciliación habilitada, lanza ``ValueError`` (el
    scheduler lo captura como fallo del trigger y aplica backoff + log).
    """

    # Mapping (no dict): covariante en el valor, así el container pasa su dict tipado.
    def __init__(self, reconcilers: Mapping[str, _Ejecutable]) -> None:
        self._reconcilers = reconcilers

    async def reconcile(self, agent_id: str) -> str:
        uc = self._reconcilers.get(agent_id)
        if uc is None:
            raise ValueError(
                f"reconcile_memory: no hay ReconcileMemoryUseCase para el agente '{agent_id}'. "
                "Verificá que memories.reconciliation.enabled=True en su config."
            )
        return await uc.execute()


class ShellExecAdapter:
    """Ejecuta triggers shell_exec como subprocess con timeout duro.

    Satisface ``IShellExecutor``. Al expirar el timeout, el proceso se MATA
    (kill + reap) — sin esto quedaba corriendo huérfano y cada retry lanzaba
    otro encima.
    """

    async def run(self, payload: ShellExecPayload) -> str:
        proc = await asyncio.create_subprocess_shell(
            payload.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=payload.working_dir,
            env={**os.environ, **(payload.env_vars or {})},
        )
        timeout = payload.timeout or 300
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            with suppress(Exception):
                await proc.communicate()  # reap — evita zombie
            raise RuntimeError(
                f"shell_exec excedió el timeout de {timeout}s — proceso terminado"
            ) from None
        if proc.returncode != 0:
            raise RuntimeError(f"shell_exec exited with code {proc.returncode}")
        return stdout.decode(errors="replace")


class HttpCallerAdapter:
    """Performs HTTP calls for webhook triggers."""

    async def call(self, payload: WebhookPayload) -> str:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.request(
                    method=payload.method,
                    url=payload.url,
                    headers=payload.headers,
                    content=payload.body,
                    timeout=payload.timeout,
                )
            except httpx.TimeoutException as exc:
                raise RuntimeError(f"Webhook timed out: {exc}") from exc
            except httpx.ConnectError as exc:
                raise RuntimeError(f"Webhook connection failed: {exc}") from exc
            if response.status_code not in payload.success_codes:
                raise RuntimeError(f"Webhook returned non-success status {response.status_code}")
            return response.text
