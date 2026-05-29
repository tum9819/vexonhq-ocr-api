"""
Unit tests for the Discord interactions endpoint (P1.4 v2 + v3,
Sessions 29 + 31).

These tests DO NOT hit the network. They use PyNaCl to locally sign
test payloads, FastAPI TestClient to drive the route, and monkeypatch
to mock out Coolify + Discord + Anthropic HTTP calls.

Coverage:
  TestVerifySignature              — 5 signature-verify cases (v2)
  TestDiscordInteractionRoute      — 6 button-click handler cases
                                     (PING, unsigned, bad sig, restart
                                     success, restart fail, show_patch
                                     dispatch, unknown custom_id)
  TestDiscordRestartTestEndpoint   — 3 manual-probe cases
  TestDiagnosisButtons             — 1 component shape test (v3)
  TestSendFollowupMessage          — 2 follow-up helper cases (v3)
  TestCoolifyFetchLogs             — 4 log-fetch parser cases (v3)
  TestShowPatchBackgroundTask      — 3 BackgroundTask end-to-end cases (v3)

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
        # discord_routes.py uses send_message_with_diagnosis_buttons (v3);
        # patch both names since the alias maps the legacy name through.
        monkeypatch.setattr(
            di, "send_message_with_diagnosis_buttons", send_mock
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


# ──────────────────────────────────────────────────────────
# v3 (Session 31) — Diagnosis buttons component shape
# ──────────────────────────────────────────────────────────
class TestDiagnosisButtons:
    def test_two_buttons_in_action_row(self):
        components = di._diagnosis_buttons()
        # Single Action Row
        assert len(components) == 1
        row = components[0]
        assert row["type"] == 1  # Action Row

        buttons = row["components"]
        assert len(buttons) == 2

        # Restart = Primary
        assert buttons[0]["type"] == 2
        assert buttons[0]["style"] == 1
        assert buttons[0]["custom_id"] == di.CUSTOM_ID_RESTART_SERVICE
        assert "Restart" in buttons[0]["label"]

        # Show patch = Secondary
        assert buttons[1]["type"] == 2
        assert buttons[1]["style"] == 2
        assert buttons[1]["custom_id"] == di.CUSTOM_ID_SHOW_PATCH
        assert "patch" in buttons[1]["label"].lower()

    def test_restart_button_alias_still_callable(self, monkeypatch):
        """Backward-compat: auto_diagnose.py calls the v2 name."""
        monkeypatch.setattr(di, "is_bot_configured", lambda: True)

        # Capture what URL/payload would be POSTed
        captured = {}

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            captured["url"] = req.full_url
            captured["body"] = req.data

            class FakeResp:
                def read(self):
                    return b'{"id": "m1", "channel_id": "c1"}'

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return FakeResp()

        monkeypatch.setattr(di.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(di, "DISCORD_BOT_TOKEN", "t")
        monkeypatch.setattr(di, "DISCORD_OPS_CHANNEL_ID", "ch")

        # Old name still works — alias points at the new function
        assert di.send_message_with_restart_button is di.send_message_with_diagnosis_buttons
        result = di.send_message_with_restart_button("hello")
        assert result == {"id": "m1", "channel_id": "c1"}

        sent = json.loads(captured["body"].decode())
        # Components should be the new 2-button shape
        assert len(sent["components"][0]["components"]) == 2


# ──────────────────────────────────────────────────────────
# v3 (Session 31) — send_followup_message helper
# ──────────────────────────────────────────────────────────
class TestSendFollowupMessage:
    def test_posts_to_webhook_path(self, monkeypatch):
        captured = {}

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["body"] = req.data

            class FakeResp:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return FakeResp()

        monkeypatch.setattr(di.urllib.request, "urlopen", fake_urlopen)

        ok = di.send_followup_message(
            "app-id-123",
            "interaction-token-xyz",
            "patch content",
        )

        assert ok is True
        assert captured["method"] == "POST"
        assert "webhooks/app-id-123/interaction-token-xyz" in captured["url"]
        # follow-up POSTs to the bare /webhooks/{id}/{token} path —
        # NOT /messages/@original (that's edit_message_via_token)
        assert "/messages/@original" not in captured["url"]
        body = json.loads(captured["body"].decode())
        assert body["content"] == "patch content"

    def test_missing_token_returns_false(self):
        assert di.send_followup_message("", "tok", "x") is False
        assert di.send_followup_message("app", "", "x") is False


# ──────────────────────────────────────────────────────────
# v3 (Session 31) — coolify_fetch_logs parser
# ──────────────────────────────────────────────────────────
class TestCoolifyFetchLogs:
    def _make_urlopen(self, body: bytes):
        """Build a fake urlopen that returns `body` from .read()."""
        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            class FakeResp:
                def read(self):
                    return body

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return FakeResp()

        return fake_urlopen

    def test_plain_text_response_tailed(self, monkeypatch):
        monkeypatch.setattr(di, "COOLIFY_API_TOKEN", "test-token")
        # 300 lines — should be tailed to last 200 by default
        lines = [f"line {i}" for i in range(300)]
        body = "\n".join(lines).encode("utf-8")
        monkeypatch.setattr(
            di.urllib.request, "urlopen", self._make_urlopen(body)
        )

        result = di.coolify_fetch_logs("uuid", tail_lines=200)
        result_lines = result.splitlines()
        assert len(result_lines) == 200
        # Last 200 lines preserved → line 100..299
        assert result_lines[0] == "line 100"
        assert result_lines[-1] == "line 299"

    def test_json_with_logs_key(self, monkeypatch):
        monkeypatch.setattr(di, "COOLIFY_API_TOKEN", "test-token")
        payload = json.dumps({"logs": "a\nb\nc"}).encode()
        monkeypatch.setattr(
            di.urllib.request, "urlopen", self._make_urlopen(payload)
        )

        result = di.coolify_fetch_logs("uuid", tail_lines=100)
        assert result == "a\nb\nc"

    def test_http_error_raises(self, monkeypatch):
        monkeypatch.setattr(di, "COOLIFY_API_TOKEN", "test-token")

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            raise di.urllib.error.HTTPError(
                req.full_url, 404, "Not Found",
                hdrs=None, fp=None,  # type: ignore[arg-type]
            )

        # urllib.error.HTTPError needs a fp to .read() — wrap to mimic
        class FakeFp:
            def read(self):
                return b"Application not found"

        def fake_urlopen_404(req, timeout=None):  # noqa: ARG001
            err = di.urllib.error.HTTPError(
                req.full_url, 404, "Not Found", hdrs=None, fp=None,  # type: ignore[arg-type]
            )
            err.read = lambda: b"Application not found"  # type: ignore[method-assign]
            raise err

        monkeypatch.setattr(
            di.urllib.request, "urlopen", fake_urlopen_404
        )

        with pytest.raises(di.CoolifyLogFetchError) as exc_info:
            di.coolify_fetch_logs("uuid")
        assert "404" in str(exc_info.value)

    def test_empty_token_raises(self, monkeypatch):
        monkeypatch.setattr(di, "COOLIFY_API_TOKEN", "")
        with pytest.raises(di.CoolifyLogFetchError):
            di.coolify_fetch_logs("uuid")


# ──────────────────────────────────────────────────────────
# v3 (Session 31) — Show patch end-to-end via interaction route
# ──────────────────────────────────────────────────────────
class TestShowPatchBackgroundTask:
    def test_show_patch_dispatches_logs_fetch_and_claude(
        self, app_with_router, keypair, monkeypatch
    ):
        sk, _ = keypair

        logs_mock = MagicMock(
            return_value="Traceback: ZeroDivisionError\n  File 'menu_routes.py', line 1234"
        )
        # Patch suggest_patch_from_logs in auto_diagnose (lazy-imported
        # inside _do_show_patch_and_followup)
        import auto_diagnose
        patch_mock = MagicMock(return_value="**สาเหตุ** — divide by zero...")
        followup_mock = MagicMock(return_value=True)
        monkeypatch.setattr(di, "coolify_fetch_logs", logs_mock)
        monkeypatch.setattr(di, "send_followup_message", followup_mock)
        monkeypatch.setattr(di, "is_coolify_configured", lambda: True)
        monkeypatch.setattr(
            auto_diagnose, "suggest_patch_from_logs", patch_mock
        )

        body_obj = {
            "type": 3,
            "data": {"custom_id": "show_patch"},
            "application_id": "test-app-id-123",
            "token": "test-interaction-token",
        }
        body = json.dumps(body_obj).encode("utf-8")
        headers = _sign_body(sk, body)

        client = TestClient(app_with_router)
        r = client.post(
            "/alerts/discord-interaction", content=body, headers=headers
        )

        assert r.status_code == 200, r.text
        # type=5: DEFERRED_CHANNEL_MESSAGE (shows "Bot is thinking...")
        assert r.json() == {"type": 5}

        # BackgroundTask ran: fetched logs → asked Claude → posted follow-up
        logs_mock.assert_called_once_with("test-backend-uuid")
        patch_mock.assert_called_once()
        followup_mock.assert_called_once()
        sent_content = followup_mock.call_args.args[2]
        assert "Patch suggestion" in sent_content
        assert "divide by zero" in sent_content

    def test_show_patch_logs_fetch_failure_posts_error(
        self, app_with_router, keypair, monkeypatch
    ):
        sk, _ = keypair

        def boom(_uuid):
            raise di.CoolifyLogFetchError(
                "Coolify logs API 502: gateway down"
            )

        followup_mock = MagicMock(return_value=True)
        monkeypatch.setattr(di, "coolify_fetch_logs", boom)
        monkeypatch.setattr(di, "send_followup_message", followup_mock)
        monkeypatch.setattr(di, "is_coolify_configured", lambda: True)

        body_obj = {
            "type": 3,
            "data": {"custom_id": "show_patch"},
            "application_id": "app",
            "token": "tok",
        }
        body = json.dumps(body_obj).encode("utf-8")
        headers = _sign_body(sk, body)

        client = TestClient(app_with_router)
        r = client.post(
            "/alerts/discord-interaction", content=body, headers=headers
        )

        assert r.status_code == 200
        assert r.json() == {"type": 5}
        followup_mock.assert_called_once()
        err_msg = followup_mock.call_args.args[2]
        assert "Couldn't fetch Coolify logs" in err_msg
        assert "gateway down" in err_msg

    def test_show_patch_empty_logs_posts_info(
        self, app_with_router, keypair, monkeypatch
    ):
        sk, _ = keypair

        monkeypatch.setattr(
            di, "coolify_fetch_logs", MagicMock(return_value="   \n\n")
        )
        followup_mock = MagicMock(return_value=True)
        monkeypatch.setattr(di, "send_followup_message", followup_mock)
        monkeypatch.setattr(di, "is_coolify_configured", lambda: True)

        body_obj = {
            "type": 3,
            "data": {"custom_id": "show_patch"},
            "application_id": "app",
            "token": "tok",
        }
        body = json.dumps(body_obj).encode("utf-8")
        headers = _sign_body(sk, body)

        client = TestClient(app_with_router)
        r = client.post(
            "/alerts/discord-interaction", content=body, headers=headers
        )

        assert r.status_code == 200
        assert r.json() == {"type": 5}
        followup_mock.assert_called_once()
        msg = followup_mock.call_args.args[2]
        assert "Coolify logs are empty" in msg


# ──────────────────────────────────────────────────────────
# Slash command branch (INTERACTION_APPLICATION_COMMAND = 2)
# ──────────────────────────────────────────────────────────
class TestApplicationCommandBranch:
    def test_resources_returns_snapshot_message(
        self, app_with_router, keypair, monkeypatch
    ):
        """POST /alerts/discord-interaction with type=2, name=resources →
        200 + RESPONSE_CHANNEL_MESSAGE containing snapshot text."""
        sk, _ = keypair
        # Force a known snapshot so the test is deterministic
        fake_snap = {
            "cpu_pct": 25.0, "ram_pct": 30.0, "ram_used_gb": 1.2,
            "ram_total_gb": 4.0, "disk_pct": 50.0, "disk_used_gb": 20.0,
            "disk_total_gb": 40.0, "swap_pct": 0.0, "swap_used_mb": 0,
            "swap_total_gb": 4.0, "scheduler_running": True,
            "scheduler_jobs": 7, "git_sha": "abc1234", "warnings": [],
        }
        monkeypatch.setattr(di, "build_resources_snapshot", lambda: fake_snap)

        body_obj = {"type": 2, "data": {"name": "resources"},
                    "application_id": "test-app-id-123", "token": "tok"}
        body = json.dumps(body_obj).encode("utf-8")
        headers = _sign_body(sk, body)

        client = TestClient(app_with_router)
        r = client.post(
            "/alerts/discord-interaction", content=body, headers=headers
        )
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["type"] == 4  # RESPONSE_CHANNEL_MESSAGE
        assert "VPS Resources" in j["data"]["content"]
        assert "abc1234" in j["data"]["content"]

    def test_unknown_command_name_returns_warning(
        self, app_with_router, keypair
    ):
        """Unknown slash-command name → 'Unsupported command' reply."""
        sk, _ = keypair
        body_obj = {"type": 2, "data": {"name": "nope"},
                    "application_id": "test-app-id-123", "token": "tok"}
        body = json.dumps(body_obj).encode("utf-8")
        headers = _sign_body(sk, body)

        client = TestClient(app_with_router)
        r = client.post(
            "/alerts/discord-interaction", content=body, headers=headers
        )
        assert r.status_code == 200
        j = r.json()
        assert j["type"] == 4
        assert "Unsupported command" in j["data"]["content"]
        assert "nope" in j["data"]["content"]

    def test_application_command_still_requires_valid_signature(
        self, app_with_router
    ):
        """Unsigned slash-command POST → 401, snapshot never built."""
        body_obj = {"type": 2, "data": {"name": "resources"}}
        body = json.dumps(body_obj).encode("utf-8")

        client = TestClient(app_with_router)
        r = client.post(
            "/alerts/discord-interaction",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 401

    def test_resources_snapshot_exception_returns_error_message(
        self, app_with_router, keypair, monkeypatch
    ):
        """If build_resources_snapshot raises, branch replies with 200 +
        '❌ /resources failed' so Discord does not show 'application did
        not respond'."""
        sk, _ = keypair

        def boom():
            raise RuntimeError("snapshot disaster")
        monkeypatch.setattr(di, "build_resources_snapshot", boom)

        body_obj = {"type": 2, "data": {"name": "resources"},
                    "application_id": "test-app-id-123", "token": "tok"}
        body = json.dumps(body_obj).encode("utf-8")
        headers = _sign_body(sk, body)

        client = TestClient(app_with_router)
        r = client.post(
            "/alerts/discord-interaction", content=body, headers=headers
        )
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["type"] == 4
        assert "❌" in j["data"]["content"]
        assert "/resources failed" in j["data"]["content"]

    def test_unknown_command_name_is_truncated_and_escaped(
        self, app_with_router, keypair
    ):
        """Long names get sliced to 32 chars; backticks become single-quotes
        so the markdown code-span around the echoed name cannot break."""
        sk, _ = keypair
        nasty_name = "back`tick" + "x" * 40  # 49 chars total, contains `
        body_obj = {"type": 2, "data": {"name": nasty_name},
                    "application_id": "test-app-id-123", "token": "tok"}
        body = json.dumps(body_obj).encode("utf-8")
        headers = _sign_body(sk, body)

        client = TestClient(app_with_router)
        r = client.post(
            "/alerts/discord-interaction", content=body, headers=headers
        )
        assert r.status_code == 200
        content = r.json()["data"]["content"]
        # Truncated: full 49-char name does NOT appear
        assert nasty_name not in content
        # Backtick escaped: the original backtick from "back`tick" is gone
        assert "back`tick" not in content
        # But the safe rendering IS present
        assert "back'tick" in content

    def test_help_command_returns_help_message(
        self, app_with_router, keypair
    ):
        """POST /alerts/discord-interaction with type=2, name=help →
        200 + RESPONSE_CHANNEL_MESSAGE containing the help text. Confirms
        the new /help dispatch branch is wired and reachable."""
        sk, _ = keypair
        body_obj = {"type": 2, "data": {"name": "help"},
                    "application_id": "test-app-id-123", "token": "tok"}
        body = json.dumps(body_obj).encode("utf-8")
        headers = _sign_body(sk, body)

        client = TestClient(app_with_router)
        r = client.post(
            "/alerts/discord-interaction", content=body, headers=headers
        )
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["type"] == 4
        content = j["data"]["content"]
        # Smoke-check: help message lists at least the canonical pieces
        assert "VEXONHQ Ops Bot" in content
        assert "/resources" in content
        assert "/help" in content
        assert "Restart" in content
