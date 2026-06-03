"""Offline unit tests for OPS-11 — the active cron stale-job watchdog.

Monkeypatches the heartbeat read + the Discord poster, so it needs no DB /
network. Run: pytest tests/test_cron_stale_alert.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")

import cron_heartbeat as ch  # noqa: E402
import auto_diagnose  # noqa: E402


def _states(stale=(), missing=()):
    jobs = [
        {"job_id": j, "stale": True, "minutes_since_last_run": 200,
         "expected_interval_hours": 1}
        for j in stale
    ]
    return (jobs, list(missing), bool(stale))


def _capture(monkeypatch):
    posts = []
    monkeypatch.setattr(auto_diagnose, "_post_to_discord",
                        lambda text: posts.append(text) or True)
    return posts


def test_stale_job_alerts_specific_id(monkeypatch):
    ch._last_stale_alert_at.clear()
    monkeypatch.setattr(ch, "_compute_job_states", lambda: _states(stale=["daily_digest"]))
    posts = _capture(monkeypatch)
    r = ch.check_and_alert_stale_jobs(now=1_700_000_000.0)
    assert r["alerted"] == ["daily_digest"]
    assert posts and "daily_digest" in posts[0]


def test_rate_limited_within_window(monkeypatch):
    ch._last_stale_alert_at.clear()
    monkeypatch.setattr(ch, "_compute_job_states", lambda: _states(stale=["j1"]))
    posts = _capture(monkeypatch)
    ch.check_and_alert_stale_jobs(now=1_700_000_000.0)
    r2 = ch.check_and_alert_stale_jobs(now=1_700_000_000.0 + 60)  # 1 min later, < 6h
    assert r2["alerted"] == []
    assert len(posts) == 1


def test_re_alerts_after_window(monkeypatch):
    ch._last_stale_alert_at.clear()
    monkeypatch.setattr(ch, "_compute_job_states", lambda: _states(stale=["j1"]))
    posts = _capture(monkeypatch)
    ch.check_and_alert_stale_jobs(now=1_700_000_000.0)
    ch.check_and_alert_stale_jobs(now=1_700_000_000.0 + 6 * 3600 + 1)  # past the 6h window
    assert len(posts) == 2


def test_no_stale_no_post(monkeypatch):
    ch._last_stale_alert_at.clear()
    monkeypatch.setattr(ch, "_compute_job_states", lambda: _states())
    posts = _capture(monkeypatch)
    r = ch.check_and_alert_stale_jobs(now=1_700_000_000.0)
    assert r == {"checked": True, "stale": [], "missing": [], "alerted": []}
    assert posts == []


def test_missing_job_alerts(monkeypatch):
    ch._last_stale_alert_at.clear()
    monkeypatch.setattr(ch, "_compute_job_states", lambda: _states(missing=["new_job"]))
    posts = _capture(monkeypatch)
    r = ch.check_and_alert_stale_jobs(now=1_700_000_000.0)
    assert "new_job" in r["alerted"]
    assert posts and "new_job" in posts[0] and "NEVER" in posts[0]


def test_self_watchdog_not_alerted_as_missing(monkeypatch):
    """The watchdog must NOT alert that it itself has never run (it writes its own
    heartbeat only after finishing, so it always looks 'missing' on first run)."""
    ch._last_stale_alert_at.clear()
    monkeypatch.setattr(ch, "_compute_job_states", lambda: _states(missing=["cron_stale_watchdog"]))
    posts = _capture(monkeypatch)
    r = ch.check_and_alert_stale_jobs(now=1_700_000_000.0)
    assert r["alerted"] == []
    assert posts == []


def test_read_failure_is_safe(monkeypatch):
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(ch, "_compute_job_states", _boom)
    posts = _capture(monkeypatch)
    r = ch.check_and_alert_stale_jobs(now=1_700_000_000.0)
    assert r == {"checked": False}
    assert posts == []
