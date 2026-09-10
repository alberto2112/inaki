"""``/admin/config`` — la config EFECTIVA con origen, editable por capa.

Regla ``config-show-effective``: una interfaz de config se construye sobre la
config efectiva con origen (``ShowEffectiveConfigUseCase``), nunca sobre los
ficheros crudos. Y regla ``borde-de-config``: una edición se valida con el MISMO
loader del arranque; ``ApplyConfigChangesUseCase`` deshace la escritura si el
loader la rechaza, así que este router nunca deja en disco algo que no arranca.

Un solo router para dos modos: montado en el admin server del daemon (auth
``X-Admin-Key``) o servido solo por ``inaki config web`` en loopback, que anula
la dependencia de auth con ``dependency_overrides``. Los secretos salen SIEMPRE
redactados; se escriben, nunca se leen.

Lo que este router necesita del mundo config lo construye ``build_config_web``
(``inaki/config/wiring.py``, el único punto que conoce loader, repo e
introspección) y quien monta la app lo deja en ``app.state.config_web``: el
router solo lo consume.
"""

from __future__ import annotations

from importlib import resources
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from inaki.channels.rest.routers.deps import check_admin_auth
from inaki.config.introspection import CampoDelSchema
from inaki.config.ports import LayerName
from inaki.config.use_cases.apply_changes import ConfigInvalidaError
from inaki.config.wiring import ConfigWeb
from inaki.shared.errors import AgentNotFoundError

router = APIRouter(prefix="/admin/config", tags=["config"])


def _web(request: Request) -> ConfigWeb:
    web = getattr(request.app.state, "config_web", None)
    if web is None:
        raise HTTPException(status_code=503, detail="La UI de config no está montada.")
    return web


class CambiosRequest(BaseModel):
    layer: str = Field(description="global | agent | sub_agent")
    agent_id: str | None = None
    cambios: dict[str, Any] = Field(default_factory=dict, description="path punteado → valor")
    heredar: list[str] = Field(default_factory=list, description="paths que vuelven a heredar")


def _metadata_por_path(schema: list[CampoDelSchema]) -> dict[str, CampoDelSchema]:
    return {c.path: c for c in schema}


def _buscar_metadata(path: str, exactos: dict[str, CampoDelSchema]) -> CampoDelSchema | None:
    """Match exacto o con comodín por segmento (``providers.*.api_key``)."""
    if path in exactos:
        return exactos[path]
    segmentos = path.split(".")
    for candidato in exactos.values():
        if "*" not in candidato.path:
            continue
        partes = candidato.path.split(".")
        if len(partes) == len(segmentos) and all(
            p == "*" or p == s for p, s in zip(partes, segmentos)
        ):
            return candidato
    return None


def _vista(web: ConfigWeb, agent_id: str | None) -> dict[str, Any]:
    vista = web.show.execute(agent_id)
    exactos = _metadata_por_path(web.schema)
    campos = []
    for c in vista.campos:
        meta = _buscar_metadata(c.path, exactos)
        campos.append(
            {
                "path": c.path,
                "valor": c.valor,
                "origen": c.origen,
                "secreto": c.es_secreto,
                "configurado": c.configurado,
                "tipo": meta.tipo if meta else None,
                "doc": meta.doc if meta else "",
                "default": meta.default if meta else None,
            }
        )
    return {"agent": agent_id, "campos": campos}


@router.get("/agents", dependencies=[Depends(check_admin_auth)])
async def listar_agentes(request: Request) -> dict[str, Any]:
    regulares, subs = _web(request).agentes()
    return {"agents": regulares, "sub_agents": subs, "daemon": _web(request).daemon}


@router.get("/effective", dependencies=[Depends(check_admin_auth)])
async def config_efectiva(request: Request, agent: str | None = None) -> dict[str, Any]:
    try:
        return _vista(_web(request), agent)
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/layer", dependencies=[Depends(check_admin_auth)])
async def editar_capa(body: CambiosRequest, request: Request) -> dict[str, Any]:
    web = _web(request)
    try:
        capa = LayerName(body.layer)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"layer inválida: {body.layer!r}") from exc
    try:
        resultado = web.apply.execute(
            capa=capa, cambios=body.cambios, heredar=body.heredar, agent_id=body.agent_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConfigInvalidaError as exc:
        # La capa ya volvió a su snapshot: el mensaje es el del loader, accionable.
        raise HTTPException(
            status_code=422, detail={"error": "config_invalida", "mensaje": str(exc)}
        ) from exc
    vista = _vista(web, body.agent_id if capa is not LayerName.GLOBAL else None)
    return {"cambiados": resultado.cambiados, "heredados": resultado.heredados, **vista}


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def ui() -> str:
    """La página. Sin auth: es HTML estático; los datos los pide con la key."""
    return resources.files("inaki.channels.rest").joinpath("static/config.html").read_text("utf-8")
