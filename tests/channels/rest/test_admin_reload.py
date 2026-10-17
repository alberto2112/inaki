"""``POST /admin/reload`` — el reload manual descarta los overrides de runtime y lo informa.

Feature ``config-set-efimero``: la puerta REST pide ``reloader.request_reload()`` (modo
manual: descarta) y devuelve en ``overrides_descartados`` una línea por cada cambio
de runtime que se tiró. La UI web usa este mismo endpoint pero no muestra el campo.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from httpx import ASGITransport, AsyncClient

from inaki.channels.rest.app import create_admin_app
from inaki.channels.rest.schemas import DaemonReloadResponse

HEADERS = {"X-Admin-Key": "clave-test"}


def _app(descartados: list[str]):
    container = MagicMock()
    container.reloader.request_reload.return_value = descartados
    return create_admin_app(container, admin_auth_key="clave-test"), container


def test_la_respuesta_trae_lista_vacia_por_defecto():
    assert DaemonReloadResponse().overrides_descartados == []


async def test_reload_devuelve_los_overrides_descartados():
    app, container = _app(["anacleto: llm.model = 'x'", "global: user.timezone = 'UTC'"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/reload", headers=HEADERS)

    assert resp.status_code == 200
    assert resp.json() == {
        "reloading": True,
        "overrides_descartados": ["anacleto: llm.model = 'x'", "global: user.timezone = 'UTC'"],
    }
    container.reloader.request_reload.assert_called_once_with()


async def test_reload_sin_overrides_devuelve_lista_vacia():
    app, _ = _app([])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/reload", headers=HEADERS)

    assert resp.json() == {"reloading": True, "overrides_descartados": []}


async def test_reload_sin_auth_no_pide_el_reload():
    app, container = _app(["x"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/admin/reload")

    assert resp.status_code == 401
    container.reloader.request_reload.assert_not_called()
