"""OPS-13: get_db_conn retries transient pooler saturation, fails fast otherwise."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "x")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")

import psycopg2  # noqa: E402
import pytest  # noqa: E402

import main  # noqa: E402


def test_retries_then_succeeds_on_max_clients(monkeypatch):
    calls = {"n": 0}
    sentinel = object()

    def fake_connect(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise psycopg2.OperationalError("FATAL: max clients reached in session mode")
        return sentinel

    monkeypatch.setattr(main.psycopg2, "connect", fake_connect)
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)
    assert main.get_db_conn() is sentinel
    assert calls["n"] == 3


def test_fails_fast_on_non_saturation_error(monkeypatch):
    calls = {"n": 0}

    def fake_connect(*a, **k):
        calls["n"] += 1
        raise psycopg2.OperationalError("FATAL: password authentication failed")

    monkeypatch.setattr(main.psycopg2, "connect", fake_connect)
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)
    with pytest.raises(psycopg2.OperationalError):
        main.get_db_conn()
    assert calls["n"] == 1  # no retry for auth errors


def test_gives_up_after_three_attempts(monkeypatch):
    calls = {"n": 0}

    def fake_connect(*a, **k):
        calls["n"] += 1
        raise psycopg2.OperationalError("max clients reached")

    monkeypatch.setattr(main.psycopg2, "connect", fake_connect)
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)
    with pytest.raises(psycopg2.OperationalError):
        main.get_db_conn()
    assert calls["n"] == 3
