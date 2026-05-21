"""
Unit tests for the DigitalOcean snapshot rotation module (P2.4, Session 31).

These tests DO NOT hit the network or the real DO API. All HTTP is
mocked via monkeypatch on urllib.request.urlopen.

Run:
    pip install pytest httpx
    pytest tests/test_do_snapshot_routes.py -v
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import do_snapshot_routes as ds  # noqa: E402


# ──────────────────────────────────────────────────────────
# Helpers — mocking the DO API
# ──────────────────────────────────────────────────────────
class _FakeResp:
    """Minimal urllib response stand-in."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _scripted_urlopen(scripts: list):
    """
    Build a urlopen replacement that returns the i-th script entry.
    Each script entry is either:
      bytes  → returned as _FakeResp(bytes)
      (int, bytes) → HTTPError(status, body)
    """
    iterator = iter(scripts)

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        try:
            entry = next(iterator)
        except StopIteration:
            raise AssertionError(
                f"unexpected extra urlopen call: {req.full_url}"
            )
        if isinstance(entry, tuple) and len(entry) == 2:
            code, body = entry
            err = ds.urllib.error.HTTPError(
                req.full_url, code, "Err",
                hdrs=None, fp=None,  # type: ignore[arg-type]
            )
            err.read = lambda: body  # type: ignore[method-assign]
            raise err
        return _FakeResp(entry)

    return fake_urlopen


# ──────────────────────────────────────────────────────────
# is_do_configured
# ──────────────────────────────────────────────────────────
class TestConfigCheck:
    def test_unconfigured_when_token_missing(self, monkeypatch):
        monkeypatch.setattr(ds, "DO_API_TOKEN", "")
        assert ds.is_do_configured() is False

    def test_configured_when_token_and_name_set(self, monkeypatch):
        monkeypatch.setattr(ds, "DO_API_TOKEN", "t")
        monkeypatch.setattr(ds, "DO_DROPLET_NAME", "core")
        assert ds.is_do_configured() is True


# ──────────────────────────────────────────────────────────
# find_droplet_id
# ──────────────────────────────────────────────────────────
class TestFindDropletId:
    def test_returns_id_when_found(self, monkeypatch):
        monkeypatch.setattr(ds, "DO_API_TOKEN", "t")
        body = json.dumps({
            "droplets": [
                {"id": 100, "name": "other-droplet"},
                {"id": 12345, "name": "vexonhq-core"},
            ]
        }).encode()
        monkeypatch.setattr(
            ds.urllib.request, "urlopen", _scripted_urlopen([body])
        )
        assert ds.find_droplet_id("vexonhq-core") == 12345

    def test_returns_none_when_not_found(self, monkeypatch):
        monkeypatch.setattr(ds, "DO_API_TOKEN", "t")
        body = json.dumps({"droplets": []}).encode()
        monkeypatch.setattr(
            ds.urllib.request, "urlopen", _scripted_urlopen([body])
        )
        assert ds.find_droplet_id("missing") is None

    def test_unauthorized_raises_do_api_error(self, monkeypatch):
        monkeypatch.setattr(ds, "DO_API_TOKEN", "bad")
        monkeypatch.setattr(
            ds.urllib.request, "urlopen",
            _scripted_urlopen([(401, b'{"id":"unauthorized"}')]),
        )
        with pytest.raises(ds.DOApiError) as exc_info:
            ds.find_droplet_id("vexonhq-core")
        assert "401" in str(exc_info.value)


# ──────────────────────────────────────────────────────────
# rotate_auto_snapshots — the orchestrator
# ──────────────────────────────────────────────────────────
class TestRotate:
    def test_keeps_one_old_auto_when_below_limit(self, monkeypatch):
        """If there's only 1 existing auto and max_keep=1, no deletion."""
        monkeypatch.setattr(ds, "DO_API_TOKEN", "t")
        monkeypatch.setattr(ds, "DO_DROPLET_NAME", "vexonhq-core")

        droplets_body = json.dumps(
            {"droplets": [{"id": 999, "name": "vexonhq-core"}]}
        ).encode()
        create_body = json.dumps(
            {"action": {"id": 5000, "status": "in-progress"}}
        ).encode()
        list_body = json.dumps({
            "snapshots": [
                {"id": 1, "name": "vexonhq-clean-base",
                 "created_at": "2026-01-01T00:00:00Z"},
                {"id": 3, "name": "vexonhq-auto-2026-05-14",
                 "created_at": "2026-05-14T20:00:00Z"},
            ]
        }).encode()

        scripted = _scripted_urlopen([droplets_body, create_body, list_body])
        monkeypatch.setattr(ds.urllib.request, "urlopen", scripted)
        monkeypatch.setattr(ds._di, "send_simple_message", MagicMock())

        report = ds.rotate_auto_snapshots(
            today=_dt.date(2026, 5, 21),
            max_keep=1,
        )

        assert report["created"] == "vexonhq-auto-2026-05-21"
        assert report["kept"] == ["vexonhq-auto-2026-05-14"]
        assert report["deleted"] == []
        assert report["errors"] == []

    def test_deletes_oldest_when_above_keep_limit(self, monkeypatch):
        """When 3 auto snapshots exist and max_keep=1, delete 2 oldest."""
        monkeypatch.setattr(ds, "DO_API_TOKEN", "t")
        monkeypatch.setattr(ds, "DO_DROPLET_NAME", "vexonhq-core")

        droplets_body = json.dumps(
            {"droplets": [{"id": 999, "name": "vexonhq-core"}]}
        ).encode()
        create_body = json.dumps(
            {"action": {"id": 5000, "status": "in-progress"}}
        ).encode()
        list_body = json.dumps({
            "snapshots": [
                {"id": 11, "name": "vexonhq-auto-2026-05-07",
                 "created_at": "2026-05-07T20:00:00Z"},
                {"id": 12, "name": "vexonhq-auto-2026-05-14",
                 "created_at": "2026-05-14T20:00:00Z"},
                {"id": 13, "name": "vexonhq-auto-2026-04-30",
                 "created_at": "2026-04-30T20:00:00Z"},
                {"id": 14, "name": "vexonhq-clean-base",
                 "created_at": "2026-01-01T00:00:00Z"},
            ]
        }).encode()

        scripted = _scripted_urlopen([
            droplets_body,  # find_droplet_id
            create_body,    # create_snapshot
            list_body,      # list
            b"",            # delete 1
            b"",            # delete 2
        ])
        monkeypatch.setattr(ds.urllib.request, "urlopen", scripted)
        monkeypatch.setattr(ds._di, "send_simple_message", MagicMock())

        report = ds.rotate_auto_snapshots(
            today=_dt.date(2026, 5, 21), max_keep=1,
        )

        # Newest is 05-14 → kept. 05-07 and 04-30 → deleted.
        # Clean-base never considered (doesn't match prefix).
        assert report["kept"] == ["vexonhq-auto-2026-05-14"]
        assert set(report["deleted"]) == {
            "vexonhq-auto-2026-05-07",
            "vexonhq-auto-2026-04-30",
        }
        assert report["errors"] == []

    def test_never_touches_clean_base_or_session_snapshots(self, monkeypatch):
        """Snapshots not matching DO_SNAPSHOT_PREFIX must NEVER be deleted."""
        monkeypatch.setattr(ds, "DO_API_TOKEN", "t")
        monkeypatch.setattr(ds, "DO_DROPLET_NAME", "vexonhq-core")

        droplets_body = json.dumps(
            {"droplets": [{"id": 999, "name": "vexonhq-core"}]}
        ).encode()
        create_body = json.dumps(
            {"action": {"id": 5000, "status": "in-progress"}}
        ).encode()
        # ONLY manually-named snapshots — no autos
        list_body = json.dumps({
            "snapshots": [
                {"id": 1, "name": "vexonhq-clean-base",
                 "created_at": "2026-01-01T00:00:00Z"},
                {"id": 2, "name": "vexonhq-session28-complete-2026-05-21",
                 "created_at": "2026-05-21T00:00:00Z"},
                {"id": 3, "name": "vexonhq-session29-complete-2026-05-21",
                 "created_at": "2026-05-21T01:00:00Z"},
                {"id": 4, "name": "vexonhq-session30-complete-2026-05-21",
                 "created_at": "2026-05-21T13:00:00Z"},
            ]
        }).encode()

        scripted = _scripted_urlopen([droplets_body, create_body, list_body])
        monkeypatch.setattr(ds.urllib.request, "urlopen", scripted)
        monkeypatch.setattr(ds._di, "send_simple_message", MagicMock())

        report = ds.rotate_auto_snapshots(
            today=_dt.date(2026, 5, 21), max_keep=1,
        )

        # Nothing in `vexonhq-auto-*` list yet → kept=[], deleted=[]
        # 4 unrelated snapshots are NEVER deleted by rotation
        assert report["kept"] == []
        assert report["deleted"] == []
        assert report["errors"] == []

    def test_unconfigured_raises(self, monkeypatch):
        monkeypatch.setattr(ds, "DO_API_TOKEN", "")
        with pytest.raises(ds.DOApiError):
            ds.rotate_auto_snapshots()

    def test_droplet_not_found_raises(self, monkeypatch):
        monkeypatch.setattr(ds, "DO_API_TOKEN", "t")
        monkeypatch.setattr(ds, "DO_DROPLET_NAME", "vexonhq-core")
        # No matching droplet
        body = json.dumps({"droplets": []}).encode()
        monkeypatch.setattr(
            ds.urllib.request, "urlopen", _scripted_urlopen([body])
        )
        with pytest.raises(ds.DOApiError) as exc_info:
            ds.rotate_auto_snapshots()
        assert "droplet not found" in str(exc_info.value)


# ──────────────────────────────────────────────────────────
# Endpoint behaviour
# ──────────────────────────────────────────────────────────
class TestEndpoints:
    @pytest.fixture()
    def client(self, monkeypatch):
        monkeypatch.setattr(ds, "ALERTS_WEBHOOK_SECRET", "secret-xyz")
        app = FastAPI()
        app.include_router(ds.router)
        return TestClient(app)

    def test_status_requires_secret(self, client):
        r = client.get("/snapshots/status")
        assert r.status_code == 401

    def test_status_returns_listing(self, client, monkeypatch):
        monkeypatch.setattr(ds, "DO_API_TOKEN", "t")
        monkeypatch.setattr(ds, "DO_DROPLET_NAME", "vexonhq-core")

        droplets_body = json.dumps(
            {"droplets": [{"id": 999, "name": "vexonhq-core"}]}
        ).encode()
        list_body = json.dumps({
            "snapshots": [
                {"id": 1, "name": "vexonhq-clean-base",
                 "created_at": "2026-01-01T00:00:00Z",
                 "size_gigabytes": 30},
                {"id": 2, "name": "vexonhq-auto-2026-05-14",
                 "created_at": "2026-05-14T20:00:00Z",
                 "size_gigabytes": 30},
            ]
        }).encode()

        monkeypatch.setattr(
            ds.urllib.request, "urlopen",
            _scripted_urlopen([droplets_body, list_body]),
        )

        r = client.get(
            "/snapshots/status", params={"secret": "secret-xyz"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["droplet"] == "vexonhq-core"
        assert body["snapshot_count"] == 2
        # First snapshot is clean-base — not auto
        assert body["snapshots"][0]["is_auto"] is False
        # Second is auto-prefixed
        assert body["snapshots"][1]["is_auto"] is True

    def test_auto_rotate_requires_secret(self, client):
        r = client.get("/snapshots/auto-rotate")
        assert r.status_code == 401

    def test_auto_rotate_returns_report(self, client, monkeypatch):
        monkeypatch.setattr(ds, "DO_API_TOKEN", "t")
        monkeypatch.setattr(ds, "DO_DROPLET_NAME", "vexonhq-core")

        # Hand-rolled scripted responses — full rotation
        droplets = json.dumps(
            {"droplets": [{"id": 999, "name": "vexonhq-core"}]}
        ).encode()
        create = json.dumps(
            {"action": {"id": 1, "status": "in-progress"}}
        ).encode()
        listing = json.dumps({"snapshots": []}).encode()  # no autos yet

        monkeypatch.setattr(
            ds.urllib.request, "urlopen",
            _scripted_urlopen([droplets, create, listing]),
        )
        monkeypatch.setattr(ds._di, "send_simple_message", MagicMock())

        r = client.get(
            "/snapshots/auto-rotate", params={"secret": "secret-xyz"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["created"].startswith("vexonhq-auto-")
        assert body["deleted"] == []
        assert body["errors"] == []
