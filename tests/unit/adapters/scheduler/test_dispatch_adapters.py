"""Unit tests for HttpCallerAdapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from adapters.outbound.scheduler.dispatch_adapters import HttpCallerAdapter
from core.domain.entities.task import WebhookPayload


def _make_payload(**kwargs: object) -> WebhookPayload:
    defaults = {"url": "https://example.com/hook"}
    defaults.update(kwargs)  # type: ignore[arg-type]
    return WebhookPayload(**defaults)  # type: ignore[arg-type]


class TestHttpCallerAdapterSuccess:
    async def test_success_200_returns_response_text(self) -> None:
        adapter = HttpCallerAdapter()
        payload = _make_payload()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "OK"

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.outbound.scheduler.dispatch_adapters.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            result = await adapter.call(payload)

        assert result == "OK"

    async def test_success_passes_method_url_headers_body_timeout(self) -> None:
        adapter = HttpCallerAdapter()
        payload = _make_payload(
            method="PUT",
            url="https://example.com/resource",
            headers={"X-Token": "abc"},
            body="payload-data",
            timeout=15,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "Updated"

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.outbound.scheduler.dispatch_adapters.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            await adapter.call(payload)

        mock_client.request.assert_awaited_once_with(
            method="PUT",
            url="https://example.com/resource",
            headers={"X-Token": "abc"},
            content="payload-data",
            timeout=15,
        )

    async def test_success_204_no_content_in_success_codes(self) -> None:
        adapter = HttpCallerAdapter()
        payload = _make_payload()

        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_response.text = ""

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.outbound.scheduler.dispatch_adapters.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            result = await adapter.call(payload)

        assert result == ""


class TestHttpCallerAdapterFailure:
    async def test_non_success_status_raises_runtime_error(self) -> None:
        adapter = HttpCallerAdapter()
        payload = _make_payload()

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.outbound.scheduler.dispatch_adapters.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            with pytest.raises(RuntimeError, match="500"):
                await adapter.call(payload)

    async def test_404_not_in_default_success_codes_raises(self) -> None:
        adapter = HttpCallerAdapter()
        payload = _make_payload()

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.outbound.scheduler.dispatch_adapters.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            with pytest.raises(RuntimeError, match="404"):
                await adapter.call(payload)

    async def test_timeout_exception_raises_runtime_error(self) -> None:
        adapter = HttpCallerAdapter()
        payload = _make_payload(timeout=5)

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=httpx.TimeoutException("Request timed out")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.outbound.scheduler.dispatch_adapters.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            with pytest.raises(RuntimeError, match="timed out"):
                await adapter.call(payload)

    async def test_custom_success_codes_accepted(self) -> None:
        adapter = HttpCallerAdapter()
        payload = _make_payload(success_codes=[201])

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.text = "Created"

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.outbound.scheduler.dispatch_adapters.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            result = await adapter.call(payload)

        assert result == "Created"

    async def test_connection_refused_raises_runtime_error(self) -> None:
        adapter = HttpCallerAdapter()
        payload = _make_payload()

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.outbound.scheduler.dispatch_adapters.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            with pytest.raises(RuntimeError, match="connection failed"):
                await adapter.call(payload)

    async def test_custom_success_codes_200_not_included_raises(self) -> None:
        adapter = HttpCallerAdapter()
        payload = _make_payload(success_codes=[201])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "OK"

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.outbound.scheduler.dispatch_adapters.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            with pytest.raises(RuntimeError, match="200"):
                await adapter.call(payload)
