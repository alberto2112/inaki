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
from inaki.config.use_cases.manage_agents import EntidadEnUsoError
from inaki.config.wiring import ConfigWeb
from inaki.shared.errors import AgentNotFoundError, AgentYaExisteError

router = APIRouter(prefix="/admin/config", tags=["config"])


def _web(request: Request) -> ConfigWeb:
    web = getattr(request.app.state, "config_web", None)
    if web is None:
        raise HTTPException(status_code=503, detail="La UI de config no está montada.")
    return web


class NuevoAgenteRequest(BaseModel):
    agent_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1)
    description: str = ""
    system_prompt: str = ""
    sub_agent: bool = False


class ProviderRequest(BaseModel):
    type: str | None = None
    base_url: str | None = None
    api_key: str | None = Field(default=None, description="write-only; vacío = no tocar")


def _422_config_invalida(exc: ConfigInvalidaError) -> HTTPException:
    # La capa ya volvió a su snapshot (o el fichero nuevo se borró): el mensaje es
    # el del loader, accionable.
    return HTTPException(status_code=422, detail={"error": "config_invalida", "mensaje": str(exc)})


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
        raise _422_config_invalida(exc) from exc
    vista = _vista(web, body.agent_id if capa is not LayerName.GLOBAL else None)
    return {"cambiados": resultado.cambiados, "heredados": resultado.heredados, **vista}


def _agentes(web: ConfigWeb) -> dict[str, Any]:
    regulares, subs = web.agentes()
    return {"agents": regulares, "sub_agents": subs}


@router.post("/agents", status_code=201, dependencies=[Depends(check_admin_auth)])
async def crear_agente(body: NuevoAgenteRequest, request: Request) -> dict[str, Any]:
    web = _web(request)
    try:
        web.manage_agents.crear(
            body.agent_id,
            body.name,
            descripcion=body.description,
            system_prompt=body.system_prompt,
            sub_agente=body.sub_agent,
        )
    except AgentYaExisteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConfigInvalidaError as exc:
        raise _422_config_invalida(exc) from exc
    return {"creado": body.agent_id, **_agentes(web)}


@router.delete("/agents/{agent_id}", dependencies=[Depends(check_admin_auth)])
async def borrar_agente(agent_id: str, request: Request, sub_agent: bool = False) -> dict[str, Any]:
    web = _web(request)
    try:
        web.manage_agents.borrar(agent_id, sub_agente=sub_agent)
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EntidadEnUsoError as exc:
        raise HTTPException(
            status_code=422, detail={"error": "en_uso", "mensaje": str(exc)}
        ) from exc
    except ConfigInvalidaError as exc:
        raise _422_config_invalida(exc) from exc
    return {"borrado": agent_id, **_agentes(web)}


def _providers(web: ConfigWeb) -> dict[str, Any]:
    return {
        "providers": [
            {"key": p.key, "type": p.type, "base_url": p.base_url, "tiene_api_key": p.tiene_api_key}
            for p in web.manage_providers.listar()
        ]
    }


@router.get("/providers", dependencies=[Depends(check_admin_auth)])
async def listar_providers(request: Request) -> dict[str, Any]:
    return _providers(_web(request))


@router.put("/providers/{key}", dependencies=[Depends(check_admin_auth)])
async def guardar_provider(key: str, body: ProviderRequest, request: Request) -> dict[str, Any]:
    web = _web(request)
    try:
        web.manage_providers.guardar(
            key, type=body.type, base_url=body.base_url, api_key=body.api_key
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConfigInvalidaError as exc:
        raise _422_config_invalida(exc) from exc
    return {"guardado": key, **_providers(web)}


@router.delete("/providers/{key}", dependencies=[Depends(check_admin_auth)])
async def borrar_provider(key: str, request: Request) -> dict[str, Any]:
    web = _web(request)
    try:
        web.manage_providers.borrar(key)
    except EntidadEnUsoError as exc:
        raise HTTPException(
            status_code=422, detail={"error": "en_uso", "mensaje": str(exc)}
        ) from exc
    except ConfigInvalidaError as exc:
        raise _422_config_invalida(exc) from exc
    return {"borrado": key, **_providers(web)}


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def ui() -> str:
    """La página. Sin auth: es HTML estático; los datos los pide con la key."""
    return resources.files("inaki.channels.rest").joinpath("static/config.html").read_text("utf-8")
