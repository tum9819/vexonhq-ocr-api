"""
Unit tests for the Discord interactions endpoint (P1.4 v2, Session 29).

These tests DO NOT hit the network. They use PyNaCl to locally sign
test payloads, FastAPI TestClient to drive the route, and monkeypatch
to mock out Coolify + Discord HTTP calls.

Run:
    pip install pytest pynacl httpx
    pytest tests/test_discord_interactions.py -v
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Repo root on sys.path so `import discord_interactions` works from tests/
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from nacl.signing import SigningKey  # noqa: E402

import discord_interactions as di  # noqa: E402
import discord_routes  # noqa: E402


# ──────────────────────────────────────────────────────────
# Test fixtures
# ──────────────────────────────────────────────────────────
@pytest.fixture()
def keypair():
    """Generate a fresh Ed25519 keypair per test."""
    sk = SigningKey.generate()
    vk_hex = sk.verify_key.encode().hex()
    return sk, vk_hex


@pytest.fixture()
def app_with_router(monkeypatch, keypair):
    """Minimal FastAPI app with just the Discord router mounted.

    Patches the module-level DISCORD_APP_PUBLIC_KEY to the test verify
    key so signed payloads pass verification.
    """
    _, vk_hex = keypair
    monkeypatch.setattr(di, "DISCORD_APP_PUBLIC_KEY", vk_hex)
    monkeypatch.setattr(di, "DISCORD_APP_ID", "test-app-id-123")
    monkeypatch.setattr(di, "COOLIFY_API_TOKEN", "test-coolify-token")
    monkeypatch.setattr(
        di, "COOLIFY_BACKEND_APP_UUID", "test-backend-uuid"
    )

    # Force re-evaluation: discord_routes captured ALERTS_WEBHOOK_SECRET
    # at import time. Patch it directly too.
    monkeypatch.setattr(
        discord_routes, "ALERTS_WEBHOOK_SECRET", "test-alerts-secret"
    )

    app = FastAPI()
    app.include_router(discord_routes.router)
    return app


def _sign_body(sk: SigningKey, body: bytes, timestamp: str = "0") -> dict:
    """Return signed headers for a Discord-style POST body."""
    message = timestamp.encode("utf-8") + body
    sig = sk.sign(message).signature.hex()
    return {
        "X-Signature-Ed25519": sig,
        "X-Signature-Timestamp": timestamp,
        "Content-Type": "application/json",
    }


# ──────────────────────────────────────────────────────────
# Pure signature-verify tests
# ──────────────────────────────────────────────────────────
class TestVerifySignature:
    def test_valid_signature(self, keypair):
        sk, vk_hex = keypair
        body = b'{"type":1}'
        timestamp = "1700000000"
        sig = sk.sign(timestamp.encode() + body).signature.hex()

        assert di.verify_signature(vk_hex, sig, timestamp, body) is True

    def test_tampered_signature_rejected(self, keypair):
        sk, vk_hex = keypair
        body = b'{"type":1}'
        timestamp = "1700000000"
        sig = sk.sign(timestamp.encode() + body).signature.hex()
        # Flip one byte of the signature
        tampered = sig[:-2] + ("00" if sig[-2:] != "00" else "ff")

        assert di.verify_signature(vk_hex, tampered, timestamp, body) is False

    def test_tampered_body_rejected(self, keypair):
        sk, vk_hex = keypair
        body = b'{"type":1}'
        timestamp = "1700000000"
        sig = sk.sign(timestamp.encode() + body).signature.hex()

        # Body changed after signing → signature no longer valid
        assert (
            di.verify_signature(vk_hex, sig, timestamp, b'{"type":3}')
            is False
        )

    def test_garbage_signature_returns_false(self, keypair):
        _, vk_hex = keypair
        assert (
            di.verify_signature(vk_hex, "not-hex", "0", b"{}") is False
        )

    def test_empty_public_key_returns_false(self):
        assert di.verify_signature("", "00" * 64, "0", b"{}") is False


# ──────────────────────────────────────────────────────────
# /alerts/discord-interaction endpoint behaviour
# ──────────────────────────────────────────────────────────
class TestDiscordInteractionRoute:
    def test_pong_on_ping(self, app_with_router, keypair):
        sk, _ = keypair
        body_obj = {"type": 1}
        body = json.dumps(body_obj).encode("utf-8")
        headers = _sign_body(sk, body)

        client = TestClient(app_with_router)
        r = client.post(
            "/alerts/discord-interaction", content=body, headers=headers
        )

        assert r.status_code == 200, r.text
        assert r.json() == {"type": 1}

    def test_unsigned_request_rejected(self, app_with_router):
        client = TestClient(app_with_router)
        r = client.post(
            "/alerts/discord-interaction",
            content=b'{"type":1}',
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 401

    def test_bad_signature_rejected(self, app_with_router):
        client = TestClient(app_with_router)
        r = client.post(
            "/alerts/discord-interaction",
            content=b'{"type":1}',
            headers={
                "Content-Type": "application/json",
                "X-Signature-Ed25519": "00" * 64,
                "X-Signature-Timestamp": "0",
            },
        )
        assert r.status_code == 401

    def test_restart_dispatches_coolify_call(
        self, app_with_router, keypair, monkeypatch
    ):
        sk, _ = keypair

        # Mock the outgoing HTTP helpers — we don't want to call the
        # real Coolify API or the real Discord webhook PATCH.
        coolify_mock = MagicMock(
            return_value={
                "message": "Restart request queued.",
                "deployment_uuid": "mock-deployment-1",
            }
        )
        edit_mock = MagicMock(return_value=True)
        monkeypatch.setattr(di, "coolify_restart", coolify_mock)
        monkeypatch.setattr(di, "edit_message_via_token", edit_mock)
        monkeypatch.setattr(di, "is_coolify_configured", lambda: True)

        body_obj = {
            "type": 3,  # MESSAGE_COMPONENT
            "data": {"custom_id": "restart_service", "component_type": 2},
            "application_id": "test-app-id-123",
            "token": "test-interaction-token",
            "message": {"id": "msg-999"},
        }
        body = json.dumps(body_obj).encode("utf-8")
        headers = _sign_body(sk, body)

        client = TestClient(app_with_router)
        r = client.post(
            "/alerts/discord-interaction", content=body, headers=headers
        )

        assert r.status_code == 200, r.text
        assert r.json() == {"type": 6}  # deferred update

        # TestClient runs BackgroundTasks synchronously after the response
        coolify_mock.assert_called_once_with("test-backend-uuid")
        edit_mock.assert_called_once()
        edited_content = edit_mock.call_args.args[2]
        assert "Restart queued" in edited_content
        assert "mock-deployment-1" in edited_content

    def test_restart_failure_edits_message_with_error(
        self, app_with_router, keypair, monkeypatch
    ):
        sk, _ = keypair

        def boom(_uuid):
            raise di.CoolifyRestartError("Coolify API 503: gateway down")

        edit_mock = MagicMock(return_value=True)
        monkeypatch.setattr(di, "coolify_restart", boom)
        monkeypatch.setattr(di, "edit_message_via_token", edit_mock)
        monkeypatch.setattr(di, "is_coolify_configured", lambda: True)

        body_obj = {
            "type": 3,
            "data": {"custom_id": "restart_service"},
            "application_id": "test-app-id-123",
            "token": "tok",
        }
        body = json.dumps(body_obj).encode("utf-8")
        headers = _sign_body(sk, body)

        client = TestClient(app_with_router)
        r = client.post(
            "/alerts/discord-interaction", content=body, headers=headers
        )

        assert r.status_code == 200
        assert r.json() == {"type": 6}
        edit_mock.assert_called_once()
        msg = edit_mock.call_args.args[2]
        assert "Restart failed" in msg
        assert "gateway down" in msg

    def test_unknown_custom_id_responds_safely(
        self, app_with_router, keypair, monkeypatch
    ):
        sk, _ = keypair
        coolify_mock = MagicMock()
        monkeypatch.setattr(di, "coolify_restart", coolify_mock)

        body_obj = {
            "type": 3,
            "data": {"custom_id": "bogus_button_id"},
            "application_id": "x",
            "token": "y",
        }
        body = json.dumps(body_obj).encode("utf-8")
        headers = _sign_body(sk, body)

        client = TestClient(app_with_router)
        r = client.post(
            "/alerts/discord-interaction", content=body, headers=headers
        )

        assert r.status_code == 200
        payload = r.json()
        assert payload["type"] == 4
        assert "Unsupported" in payload["data"]["content"]
        coolify_mock.assert_not_called()


# ──────────────────────────────────────────────────────────
# /alerts/discord-restart-test (manual probe) behaviour
# ──────────────────────────────────────────────────────────
class TestDiscordRestartTestEndpoint:
    def test_requires_secret(self, app_with_router):
        client = TestClient(app_with_router)
        r = client.get("/alerts/discord-restart-test")
        assert r.status_code == 401

    def test_wrong_secret_rejected(self, app_with_router):
        client = TestClient(app_with_router)
        r = client.get(
            "/alerts/discord-restart-test", params={"secret": "wrong"}
        )
        assert r.status_code == 401

    def test_with_secret_posts_to_discord(
        self, app_with_router, monkeypatch
    ):
        send_mock = MagicMock(
            return_value={"id": "msg-1", "channel_id": "chan-1"}
        )
        monkeypatch.setattr(
            di, "send_message_with_restart_button", send_mock
        )
        monkeypatch.setattr(di, "is_bot_configured", lambda: True)

        client = TestClient(app_with_router)
        r = client.get(
            "/alerts/discord-restart-test",
            params={"secret": "test-alerts-secret"},
        )

        assert r.status_code == 200, r.text
        assert r.json() == {
            "ok": True,
            "discord_message_id": "msg-1",
            "channel_id": "chan-1",
        }
        send_mock.assert_called_once()
