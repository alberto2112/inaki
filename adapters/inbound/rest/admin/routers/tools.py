"""Router de tools admin — endpoints para listar, invocar tools y enviar mensajes.

Expone tres endpoints:
  GET  /admin/tool/list    — lista las tools registradas en un agente
  POST /admin/tool/invoke  — invoca una tool con argumentos arbitrarios
  POST /admin/send         — envía un mensaje a un canal externo via ChannelOutboundRegistry

Todos requieren X-Admin-Key (via check_admin_auth).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from adapters.inbound.rest.admin.routers.deps import check_admin_auth, resolver_agente
from adapters.inbound.rest.admin.schemas import (
    SendRequest,
    SendResponse,
    ToolInvokeRequest,
    ToolInvokeResponse,
    ToolListEntry,
    ToolListResponse,
)
from core.domain.value_objects.outbound_kind import OutboundKind

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /admin/tool/list
# ---------------------------------------------------------------------------


@router.get(
    "/admin/tool/list",
    response_model=ToolListResponse,
    dependencies=[Depends(check_admin_auth)],
)
async def list_tools(agent_id: str, request: Request) -> ToolListResponse:
    """Lista las tools registradas en el agente dado.

    Extrae los schemas del ToolRegistry y los convierte a ToolListEntry,
    descartando el wrapper OpenAI (type/function) para exponer solo los
    campos relevantes al operador.
    """
    agente = resolver_agente(request, agent_id)
    schemas_openai = agente._tools.get_schemas()

    entries: list[ToolListEntry] = []
    for wrapper in schemas_openai:
        fn = wrapper.get("function", {})
        entries.append(
            ToolListEntry(
                name=fn.get("name", ""),
                description=fn.get("description", ""),
                parameters_schema=fn.get("parameters", {}),
            )
        )

    logger.debug("list_tools agent=%s count=%d", agent_id, len(entries))
    return ToolListResponse(tools=entries)


# ---------------------------------------------------------------------------
# POST /admin/tool/invoke
# ---------------------------------------------------------------------------


@router.post(
    "/admin/tool/invoke",
    response_model=ToolInvokeResponse,
    dependencies=[Depends(check_admin_auth)],
)
async def invoke_tool(body: ToolInvokeRequest, request: Request) -> ToolInvokeResponse:
    """Invoca una tool del agente con los argumentos dados.

    Si la tool no existe, el ToolRegistry devuelve ToolResult(success=False) →
    se mapea a HTTP 200 con success=False en el payload (no es 404: el endpoint
    resuelve agentes, no tools individuales).

    Si execute() lanza excepción inesperada (no debería, el registry tiene catch
    general), devuelve 500.
    """
    agente = resolver_agente(request, body.agent_id)

    try:
        resultado = await agente._tools.execute(body.tool_name, **body.args)
    except Exception as exc:
        logger.exception(
            "invoke_tool error inesperado agent=%s tool=%s", body.agent_id, body.tool_name
        )
        raise HTTPException(
            status_code=500,
            detail={"error": str(exc), "error_code": "internal_error"},
        ) from exc

    logger.debug(
        "invoke_tool agent=%s tool=%s success=%s", body.agent_id, body.tool_name, resultado.success
    )
    return ToolInvokeResponse(
        tool_name=resultado.tool_name,
        output=resultado.output,
        success=resultado.success,
        error=resultado.error,
    )


# ---------------------------------------------------------------------------
# POST /admin/send
# ---------------------------------------------------------------------------


@router.post(
    "/admin/send",
    response_model=SendResponse,
    dependencies=[Depends(check_admin_auth)],
)
async def send_message(body: SendRequest, request: Request) -> SendResponse:
    """Envía un mensaje a un canal externo via ChannelOutboundRegistry del agente.

    Flujo:
      1. Resolver agente o 404
      2. Resolver adapter del canal o 404 (channel_not_registered)
      3. Validar que el adapter soporta el kind o 422 (unsupported_kind)
      4. Convertir sources str → Path
      5. Llamar adapter.send()
      6. Retornar SendResponse

    Errores del adapter:
      ValueError    → 422 validation_error
      FileNotFoundError → 404 source_not_found
      RuntimeError con "no está disponible" → 503 channel_unavailable
      Otras → 500 internal_error
    """
    agente = resolver_agente(request, body.agent_id)
    registry = agente.channel_outbound_registry

    # Resolver adapter del canal
    try:
        adapter = registry.get(body.channel)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": f"Canal '{body.channel}' no registrado en el agente '{body.agent_id}'",
                "error_code": "channel_not_registered",
                "disponibles": registry.list_channels(),
            },
        )

    # Validar capability
    kind_enum = OutboundKind(body.kind)
    if kind_enum not in adapter.capabilities():
        raise HTTPException(
            status_code=422,
            detail={
                "error": f"El canal '{body.channel}' no soporta kind='{body.kind}'",
                "error_code": "unsupported_kind",
                "supported": [k.value for k in adapter.capabilities()],
            },
        )

    # Convertir sources a Path
    sources_path: list[Path] | None = (
        [Path(s) for s in body.sources] if body.sources is not None else None
    )

    # Invocar el adapter
    try:
        await adapter.send(
            chat_id=body.chat_id,
            kind=kind_enum,
            text=body.text,
            sources=sources_path,
            caption=body.caption,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": str(exc), "error_code": "validation_error"},
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": str(exc), "error_code": "source_not_found"},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": str(exc), "error_code": "channel_unavailable"},
        ) from exc
    except Exception as exc:
        logger.exception(
            "send_message error inesperado agent=%s channel=%s kind=%s",
            body.agent_id,
            body.channel,
            body.kind,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": str(exc), "error_code": "internal_error"},
        ) from exc

    logger.info(
        "send_message ok agent=%s channel=%s chat_id=%s kind=%s",
        body.agent_id,
        body.channel,
        body.chat_id,
        body.kind,
    )
    return SendResponse(
        sent=True,
        channel=body.channel,
        chat_id=body.chat_id,
        kind=body.kind,
    )
