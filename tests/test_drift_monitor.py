"""
test_drift_monitor.py — offline checks for the AI drift watcher's PURE detection
(audit roadmap final item). No DB / no network / no API key: drives evaluate_drift
+ the renderers with synthetic dict rows. Proves the adversarial false-positive
design actually holds.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import drift_monitor as dm
from drift_monitor import evaluate_drift, render_discord_message, render_digest_line, THRESHOLDS

NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)        # well past cold-start
OLD = 60.0                                               # oldest row age (days) — armed-eligible
COLD = 10.0                                              # within 28-day lockout


def _task(calls, persistent_fails=0, transient_fails=0, ok_calls=None,
          p50=None, avg_tokens=None, model="gpt-4o", days=21):
    return {
        "calls": calls, "persistent_fails": persistent_fails,
        "transient_fails": transient_fails,
        "ok_calls": ok_calls if ok_calls is not None else calls - persistent_fails - transient_fails,
        "p50_latency_ms": p50, "avg_tokens": avg_tokens,
        "dominant_model": model, "days_span": days,
    }


def _arm(monkeypatch, on=True):
    monkeypatch.setenv("AI_DRIFT_ALERTS_ARMED", "1" if on else "0")


def _by_rule(findings, rule):
    return [f for f in findings if f.rule == rule]


# ── cold start ────────────────────────────────────────────────────────────────
def test_cold_start_suppresses_all_pings(monkeypatch):
    _arm(monkeypatch)
    recent = {"vision_ocr": _task(200, persistent_fails=120)}     # 60% errors
    base = {"vision_ocr": _task(300, persistent_fails=3)}
    fs = evaluate_drift(recent, base, COLD, {}, NOW)
    assert all(not f.should_post for f in fs)   # nothing posts during warm-up


# ── sparse gate: the classic n=4 false page is impossible ──────────────────────
def test_sparse_task_insufficient_data(monkeypatch):
    _arm(monkeypatch)
    recent = {"narrative": _task(4, persistent_fails=2)}          # 50% but n=4
    base = {"narrative": _task(8, persistent_fails=0)}
    fs = evaluate_drift(recent, base, OLD, {}, NOW)
    assert _by_rule(fs, "ERROR_RATE_REGRESSION") == []
    assert _by_rule(fs, "ERROR_FLOOR") == []


# ── transient vs persistent split ──────────────────────────────────────────────
def test_transient_storm_does_not_fire(monkeypatch):
    _arm(monkeypatch)
    # 40% transient (529-style), ~1% persistent → must NOT fire
    recent = {"vision_ocr": _task(120, persistent_fails=1, transient_fails=48)}
    base = {"vision_ocr": _task(300, persistent_fails=3)}
    fs = evaluate_drift(recent, base, OLD, {}, NOW)
    assert _by_rule(fs, "ERROR_RATE_REGRESSION") == []
    # transient-dominated? 48/120=0.4 < 0.5 so not outage-suppressed here
    assert _by_rule(fs, "OUTAGE_SUPPRESSED") == []


def test_persistent_errors_do_fire(monkeypatch):
    _arm(monkeypatch)
    recent = {"vision_ocr": _task(120, persistent_fails=18)}      # 15% real errors
    base = {"vision_ocr": _task(300, persistent_fails=3)}          # 1%
    fs = evaluate_drift(recent, base, OLD, {}, NOW)
    reg = _by_rule(fs, "ERROR_RATE_REGRESSION")
    assert reg, "a 1%→15% persistent jump must produce a regression finding"


# ── two-proportion gate (small lift cannot fire) ───────────────────────────────
def test_small_lift_does_not_fire(monkeypatch):
    _arm(monkeypatch)
    recent = {"categorize": _task(120, persistent_fails=6)}       # 5%
    base = {"categorize": _task(300, persistent_fails=3)}          # 1% → lift 4pp < 10pp
    fs = evaluate_drift(recent, base, OLD, {}, NOW)
    assert _by_rule(fs, "ERROR_RATE_REGRESSION") == []


# ── persistence → escalation → dedup ───────────────────────────────────────────
def test_persistence_escalation_and_dedup(monkeypatch):
    _arm(monkeypatch)
    recent = {"vision_ocr": _task(150, persistent_fails=30)}      # 20%
    base = {"vision_ocr": _task(300, persistent_fails=3)}          # 1%

    # Run 1: no prior state → WARN (digest), does NOT ping.
    fs1 = evaluate_drift(recent, base, OLD, {}, NOW)
    reg1 = _by_rule(fs1, "ERROR_RATE_REGRESSION")[0]
    assert reg1.severity == "WARN" and reg1.channel == "digest" and not reg1.should_post

    # Run 2: prior state seen a run ago → escalates to CRIT and pings.
    prev = {"vision_ocr:ERROR_RATE_REGRESSION":
            {"severity": "WARN", "first_seen_at": NOW - timedelta(days=1), "last_posted_at": None}}
    fs2 = evaluate_drift(recent, base, OLD, prev, NOW)
    reg2 = _by_rule(fs2, "ERROR_RATE_REGRESSION")[0]
    assert reg2.severity == "CRIT" and reg2.channel == "ping" and reg2.should_post

    # Run 3: already CRIT + posted just now → deduped (within 7-day cooldown).
    prev2 = {"vision_ocr:ERROR_RATE_REGRESSION":
             {"severity": "CRIT", "first_seen_at": NOW - timedelta(days=2), "last_posted_at": NOW}}
    fs3 = evaluate_drift(recent, base, OLD, prev2, NOW)
    assert not _by_rule(fs3, "ERROR_RATE_REGRESSION")[0].should_post

    # After cooldown → one reminder.
    prev3 = {"vision_ocr:ERROR_RATE_REGRESSION":
             {"severity": "CRIT", "first_seen_at": NOW - timedelta(days=20),
              "last_posted_at": NOW - timedelta(days=8)}}
    fs4 = evaluate_drift(recent, base, OLD, prev3, NOW)
    assert _by_rule(fs4, "ERROR_RATE_REGRESSION")[0].should_post


# ── ERROR_FLOOR independent of a poisoned baseline ─────────────────────────────
def test_error_floor_fires_even_with_high_baseline(monkeypatch):
    _arm(monkeypatch)
    recent = {"vision_ocr": _task(100, persistent_fails=50)}      # 50%
    base = {"vision_ocr": _task(300, persistent_fails=135)}        # 45% baseline (poisoned)
    fs = evaluate_drift(recent, base, OLD, {}, NOW)
    floor = _by_rule(fs, "ERROR_FLOOR")
    assert floor and floor[0].severity == "CRIT" and floor[0].should_post


# ── zero baseline rule-of-three ────────────────────────────────────────────────
def test_zero_baseline_rule_of_three(monkeypatch):
    _arm(monkeypatch)
    base = {"categorize": _task(300, persistent_fails=0)}
    # 5% recent < max(0.10, 3/300) → no fire
    fs_lo = evaluate_drift({"categorize": _task(120, persistent_fails=6)}, base, OLD, {}, NOW)
    assert _by_rule(fs_lo, "ERROR_RATE_REGRESSION") == []
    # 12.5% recent ≥ 0.10 floor → fire
    fs_hi = evaluate_drift({"categorize": _task(120, persistent_fails=15)}, base, OLD, {}, NOW)
    assert _by_rule(fs_hi, "ERROR_RATE_REGRESSION")


# ── model change suppresses latency/token, never pings ─────────────────────────
def test_model_change_suppresses_latency_token(monkeypatch):
    _arm(monkeypatch)
    recent = {"categorize": _task(120, p50=4000, avg_tokens=2000, model="gpt-4o-mini")}
    base = {"categorize": _task(300, ok_calls=300, p50=1000, avg_tokens=500, model="gpt-4o")}
    fs = evaluate_drift(recent, base, OLD, {}, NOW)
    assert _by_rule(fs, "MODEL_CHANGE")
    assert _by_rule(fs, "LATENCY_DRIFT") == []   # suppressed
    assert _by_rule(fs, "TOKEN_PROFILE") == []   # suppressed
    assert all(not f.should_post for f in fs)    # INFO never pings


# ── outage-dedup guard ─────────────────────────────────────────────────────────
def test_outage_guard_suppresses_task(monkeypatch):
    _arm(monkeypatch)
    recent = {"vision_ocr": _task(100, persistent_fails=20, transient_fails=60)}  # 60% transient
    base = {"vision_ocr": _task(300, persistent_fails=3)}
    fs = evaluate_drift(recent, base, OLD, {}, NOW)
    assert _by_rule(fs, "OUTAGE_SUPPRESSED")
    # task is fully suppressed → no regression/floor for it
    assert _by_rule(fs, "ERROR_RATE_REGRESSION") == []
    assert _by_rule(fs, "ERROR_FLOOR") == []


# ── latency drift is digest-only ───────────────────────────────────────────────
def test_latency_drift_never_pings(monkeypatch):
    _arm(monkeypatch)
    recent = {"vision_ocr": _task(120, ok_calls=120, p50=5000)}
    base = {"vision_ocr": _task(300, ok_calls=300, p50=1500)}
    fs = evaluate_drift(recent, base, OLD, {}, NOW)
    lat = _by_rule(fs, "LATENCY_DRIFT")
    assert lat and lat[0].channel == "digest" and not lat[0].should_post


# ── armed flag: identical drift, unarmed → no post ─────────────────────────────
def test_unarmed_never_posts(monkeypatch):
    _arm(monkeypatch, on=False)
    recent = {"vision_ocr": _task(100, persistent_fails=50)}
    base = {"vision_ocr": _task(300, persistent_fails=3)}
    fs = evaluate_drift(recent, base, OLD, {}, NOW)
    assert any(f.rule == "ERROR_FLOOR" for f in fs)          # finding exists
    assert all(not f.should_post for f in fs)                # but nothing posts


# ── renderers ──────────────────────────────────────────────────────────────────
def test_render_clean_day_is_silent(monkeypatch):
    _arm(monkeypatch)
    fs = evaluate_drift({"vision_ocr": _task(120, persistent_fails=1)},
                        {"vision_ocr": _task(300, persistent_fails=2)}, OLD, {}, NOW)
    assert render_discord_message(fs) is None       # clean → no message
    assert "ปกติ" in render_digest_line(fs)


def test_render_message_bounded(monkeypatch):
    _arm(monkeypatch)
    recent = {"vision_ocr": _task(100, persistent_fails=50)}
    base = {"vision_ocr": _task(300, persistent_fails=3)}
    fs = evaluate_drift(recent, base, OLD, {}, NOW)
    msg = render_discord_message(fs)
    assert msg and len(msg) <= 1900 and "AI Drift" in msg


def test_two_proportion_math():
    # sanity on the stats helper: a clear regression is significant (>0); a recent
    # rate at/below baseline is not. (The 10pp lift gate in the RULE handles the
    # borderline-but-tiny-lift cases — see test_small_lift_does_not_fire.)
    assert dm._two_proportion_lower_bound(18, 120, 3, 300, 0.99) > 0     # 15% vs 1% → clear
    assert dm._two_proportion_lower_bound(1, 120, 3, 300, 0.95) <= 0     # 0.8% vs 1% → not worse
