"""Ports de despacho del scheduler — contratos que el SchedulerService consume.

``SchedulerDispatchPorts`` vivía en ``adapters/outbound/scheduler/dispatch_adapters.py``
tipado con las clases concretas, y ``core/`` lo importaba (violación hexagonal).
Acá se declara la superficie REAL que ``SchedulerService._dispatch_trigger`` usa;
los adapters concretos (``ChannelRouter``, ``ConsolidationDispatchAdapter``,
``HttpCallerAdapter``, ``LLMDispatcherAdapter``) los satisfacen estructuralmente.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.domain.entities.task import ShellExecPayload, WebhookPayload
from core.domain.value_objects.dispatch_result import DispatchResult
from core.ports.outbound.intermediate_sink_port import IIntermediateSink
from core.ports.outbound.llm_dispatcher_port import ILLMDispatcher


class IChannelSender(Protocol):
    """Resuelve un ``target`` (ej: ``"telegram:123"``) y entrega texto a ese canal."""

    async def send_message(self, target: str, text: str) -> DispatchResult: ...

    def build_intermediate_sink(self, target: str) -> IIntermediateSink: ...


class IConsolidator(Protocol):
    """Dispara la consolidación de memoria de todos los agentes."""

    async def consolidate_all(self) -> str: ...


class IReconciler(Protocol):
    """Dispara la reconciliación de memoria de un agente concreto."""

    async def reconcile(self, agent_id: str) -> str: ...


class IHttpCaller(Protocol):
    """Ejecuta un trigger webhook contra una URL externa."""

    async def call(self, payload: WebhookPayload) -> str: ...


class IShellExecutor(Protocol):
    """Ejecuta un trigger shell_exec como subprocess controlado.

    El subprocess es I/O de sistema operativo — adapter, no dominio. El
    contrato exige timeout duro: al expirar, el proceso DEBE ser terminado
    (kill), no abandonado corriendo.
    """

    async def run(self, payload: ShellExecPayload) -> str: ...


class IChannelHistoryRecorder(Protocol):
    """Persiste el texto de un ``channel_send`` como mensaje del asistente en el
    historial del agente dueño de la tarea.

    Un ``channel_send`` es el asistente emitiendo un mensaje dentro de una
    conversación — sin esto, el agente no tendría rastro en su historial de lo
    que envió y perdería la continuidad si el usuario responde. Simetría con
    ``agent_send``, que ya persiste su intercambio vía ``llm_dispatcher``.

    El recorder es el ÚNICO que conoce qué canales son conversacionales y cómo
    resolver el historial de cada agente — el ``SchedulerService`` solo delega.
    Implementaciones que apunten a un fallback no-conversacional (ej: archivo)
    o a un agente desconocido DEBEN ser no-op.
    """

    async def record_channel_send(
        self, agent_id: str, resolved_target: str, text: str
    ) -> None: ...


@dataclass(frozen=True)
class SchedulerDispatchPorts:
    """Bundle de ports que el ``SchedulerService`` recibe en el constructor."""

    channel_sender: IChannelSender
    llm_dispatcher: ILLMDispatcher
    consolidator: IConsolidator
    reconciler: IReconciler
    http_caller: IHttpCaller
    shell_executor: IShellExecutor
    history_recorder: IChannelHistoryRecorder
