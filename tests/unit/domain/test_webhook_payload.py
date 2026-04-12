"""Unit tests for WebhookPayload domain model."""

from __future__ import annotations

import json

from core.domain.entities.task import TriggerPayload, WebhookPayload


class TestWebhookPayloadDefaults:
    def test_required_field_url(self) -> None:
        payload = WebhookPayload(url="https://example.com/hook")
        assert payload.url == "https://example.com/hook"

    def test_default_method_is_post(self) -> None:
        payload = WebhookPayload(url="https://example.com/hook")
        assert payload.method == "POST"

    def test_default_headers_is_empty_dict(self) -> None:
        payload = WebhookPayload(url="https://example.com/hook")
        assert payload.headers == {}

    def test_default_body_is_none(self) -> None:
        payload = WebhookPayload(url="https://example.com/hook")
        assert payload.body is None

    def test_default_timeout_is_30(self) -> None:
        payload = WebhookPayload(url="https://example.com/hook")
        assert payload.timeout == 30

    def test_default_success_codes(self) -> None:
        payload = WebhookPayload(url="https://example.com/hook")
        assert payload.success_codes == [200, 201, 202, 204]

    def test_type_discriminator_is_webhook(self) -> None:
        payload = WebhookPayload(url="https://example.com/hook")
        assert payload.type == "webhook"


class TestWebhookPayloadCustomFields:
    def test_custom_method(self) -> None:
        payload = WebhookPayload(url="https://example.com/hook", method="GET")
        assert payload.method == "GET"

    def test_custom_headers(self) -> None:
        payload = WebhookPayload(
            url="https://example.com/hook",
            headers={"Authorization": "Bearer token", "Content-Type": "application/json"},
        )
        assert payload.headers["Authorization"] == "Bearer token"
        assert payload.headers["Content-Type"] == "application/json"

    def test_custom_body(self) -> None:
        payload = WebhookPayload(url="https://example.com/hook", body='{"key": "value"}')
        assert payload.body == '{"key": "value"}'

    def test_custom_timeout(self) -> None:
        payload = WebhookPayload(url="https://example.com/hook", timeout=60)
        assert payload.timeout == 60

    def test_custom_success_codes(self) -> None:
        payload = WebhookPayload(url="https://example.com/hook", success_codes=[200, 204])
        assert payload.success_codes == [200, 204]


class TestWebhookPayloadJsonRoundTrip:
    def test_json_round_trip_defaults(self) -> None:
        payload = WebhookPayload(url="https://example.com/hook")
        dumped = payload.model_dump()
        restored = WebhookPayload(**dumped)
        assert restored == payload

    def test_json_serialization_includes_type_discriminator(self) -> None:
        payload = WebhookPayload(url="https://example.com/hook")
        data = json.loads(payload.model_dump_json())
        assert data["type"] == "webhook"

    def test_discriminated_union_roundtrip(self) -> None:
        """WebhookPayload deserializes correctly via the TriggerPayload union."""
        from pydantic import TypeAdapter

        adapter = TypeAdapter(TriggerPayload)
        raw = {
            "type": "webhook",
            "url": "https://example.com/hook",
            "method": "PUT",
            "timeout": 10,
        }
        parsed = adapter.validate_python(raw)
        assert isinstance(parsed, WebhookPayload)
        assert parsed.url == "https://example.com/hook"
        assert parsed.method == "PUT"
        assert parsed.timeout == 10
