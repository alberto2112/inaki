"""Tests para DaemonClient — adapter HTTP para comunicación con el daemon."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from adapters.outbound.daemon_client import DaemonClient
from core.domain.errors import (
    DaemonClientError,
    DaemonNotRunningError,
    DaemonTimeoutError,
)


@pytest.fixture
def client() -> DaemonClient:
    return DaemonClient(admin_base_url="http://127.0.0.1:6497", auth_key="test-key")


@pytest.fixture
def client_no_auth() -> DaemonClient:
    return DaemonClient(admin_base_url="http://127.0.0.1:6497", auth_key=None)


# ---------------------------------------------------------------------------
# health()
# ---------------------------------------------------------------------------


def test_health_returns_true_on_200(client: DaemonClient) -> None:
    with patch("httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        assert client.health() is True


def test_health_returns_false_on_connect_error(client: DaemonClient) -> None:
    import httpx

    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        assert client.health() is False


def test_health_returns_false_on_timeout(client: DaemonClient) -> None:
    import httpx

    with patch("httpx.get", side_effect=httpx.TimeoutException("timeout")):
        assert client.health() is False


def test_health_returns_false_on_non_200(client: DaemonClient) -> None:
    with patch("httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=503)
        assert client.health() is False


# ---------------------------------------------------------------------------
# scheduler_reload()
# ---------------------------------------------------------------------------


def test_scheduler_reload_returns_true_on_200(client: DaemonClient) -> None:
    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        assert client.scheduler_reload() is True


def test_scheduler_reload_returns_false_on_connect_error(client: DaemonClient) -> None:
    import httpx

    with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
        assert client.scheduler_reload() is False


def test_scheduler_reload_returns_false_on_timeout(client: DaemonClient) -> None:
    import httpx

    with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
        assert client.scheduler_reload() is False


def test_scheduler_reload_sends_auth_header(client: DaemonClient) -> None:
    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        client.scheduler_reload()
        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["X-Admin-Key"] == "test-key"


def test_scheduler_reload_no_auth_header_when_no_key(client_no_auth: DaemonClient) -> None:
    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        client_no_auth.scheduler_reload()
        _, kwargs = mock_post.call_args
        assert "X-Admin-Key" not in kwargs.get("headers", {})


# ---------------------------------------------------------------------------
# inspect()
# ---------------------------------------------------------------------------


def test_inspect_returns_dict_on_200(client: DaemonClient) -> None:
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"resultado": "ok"}
        mock_post.return_value = mock_resp
        result = client.inspect("general", "hola")
        assert result == {"resultado": "ok"}


def test_inspect_raises_not_running_on_connect_error(client: DaemonClient) -> None:
    import httpx

    with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(DaemonNotRunningError):
            client.inspect("general", "hola")


def test_inspect_raises_timeout_error(client: DaemonClient) -> None:
    import httpx

    with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
        with pytest.raises(DaemonTimeoutError):
            client.inspect("general", "hola")


def test_inspect_raises_client_error_on_non_2xx(client: DaemonClient) -> None:
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=403)
        mock_resp.text = "Forbidden"
        mock_post.return_value = mock_resp
        with pytest.raises(DaemonClientError) as exc_info:
            client.inspect("general", "hola")
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# consolidate()
# ---------------------------------------------------------------------------


def test_consolidate_returns_dict_on_200(client: DaemonClient) -> None:
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"status": "ok"}
        mock_post.return_value = mock_resp
        result = client.consolidate("general")
        assert result == {"status": "ok"}


def test_consolidate_raises_not_running_on_connect_error(client: DaemonClient) -> None:
    import httpx

    with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(DaemonNotRunningError):
            client.consolidate("general")


def test_consolidate_raises_timeout_error(client: DaemonClient) -> None:
    import httpx

    with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
        with pytest.raises(DaemonTimeoutError):
            client.consolidate()


def test_consolidate_raises_client_error_on_500(client: DaemonClient) -> None:
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock(status_code=500)
        mock_resp.text = "Internal Server Error"
        mock_post.return_value = mock_resp
        with pytest.raises(DaemonClientError) as exc_info:
            client.consolidate()
        assert exc_info.value.status_code == 500
