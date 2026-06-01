"""
drift_monitor.py — AI quality/cost drift watcher (audit roadmap, final item).

Reads public.ai_call_log (written by llm.py) and detects when an AI feature's
behaviour DEGRADES — primarily a rising persistent-error rate per task — and (when
armed) posts a high-signal alert to the Ops Discord. Adversarially designed to NOT
cry wolf: a noisy alert would train a non-dev owner to mute the channel, killing
every ops alert.

SCOPE (v1): this is a QUALITY/COST drift watcher, NOT an outage detector. Outages
stay owned by /health/deep + Uptime Robot + auto_diagnose. The Discord push channel
carries EXACTLY ONE signal — a per-task persistent-error-rate regression. Latency /
token / cost / model-change / volume signals are DIGEST-ONLY (GET /ai/drift + one
folded line in the Monday weekly_summary), never a push.

This module splits into:
  - PURE detection (`evaluate_drift`, the THRESHOLDS dict, the renderers) — no DB,
    no network, no API key → unit-testable with synthetic dict rows.
  - an IO wrapper (`run_drift_check`) that does the SQL + reads/writes ai_drift_state
    + conditionally calls auto_diagnose._post_to_discord. The dedup/escalation
    DECISION lives inside the pure function via `prev_state`.

What it CAN detect: a >=10pp persistent-error-rate lift at ~80% power once a task
does >=30 calls/7d with a >=100-call baseline. What it CANNOT: drift in sub-30-call
tasks (silent by design); token/cost drift confounded with invoice page-count
(digest-only, labelled). See evaluate_drift's contract below.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

log = logging.getLogger("drift_monitor")

# ── All tunables in one place (one-line retuning) ─────────────────────────────
THRESHOLDS: dict[str, Any] = {
    "RECENT_DAYS": 7,
    "BASELINE_DAYS": 21,
    "BASELINE_OFFSET": 7,          # baseline ends where recent begins (non-overlap)
    "COLD_START_DAYS": 28,         # = BASELINE_OFFSET + BASELINE_DAYS
    # error-rate regression gates
    "ER_MIN_RECENT_CALLS": 30,
    "ER_MIN_RECENT_FAILS": 5,
    "ER_MIN_BASELINE_CALLS": 100,
    "ER_MIN_ABS_LIFT": 0.10,       # >= 10 percentage points
    "ER_WARN_CONF": 0.95,
    "ER_CRIT_CONF": 0.99,
    # baseline-independent "broken" floor
    "FLOOR_RATE": 0.40,
    "FLOOR_MIN_RECENT_CALLS": 30,
    "FLOOR_MIN_RECENT_FAILS": 10,
    # latency / token (digest only)
    "LAT_RATIO": 1.75,
    "LAT_MIN_ABS_MS": 1500,
    "LAT_MIN_RECENT": 20,
    "TOKEN_RATIO": 2.0,
    "TOKEN_MIN_BASELINE": 20,
    # volume collapse (digest only)
    "VOL_MIN_BASELINE_PER_DAY": 5,
    # outage-dedup guard
    "OUTAGE_TRANSIENT_RATE": 0.50,
    # dedup
    "DEDUP_COOLDOWN_DAYS": 7,
}

# An error counts toward "drift" ONLY if it's a persistent/client error. Transient
# transport errors are a provider/outage concern, not model drift.
PERSISTENT_STATUSES = frozenset({400, 401, 403, 404, 422})
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504, 529})  # NULL status also transient


def _is_armed() -> bool:
    return os.environ.get("AI_DRIFT_ALERTS_ARMED", "0").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Finding:
    task: str
    rule: str
    severity: str            # "INFO" | "WARN" | "CRIT"
    channel: str             # "digest" | "ping"
    message_th: str
    observed: float = 0.0
    baseline: float = 0.0
    should_post: bool = False  # decided in evaluate_drift using prev_state + armed

    @property
    def key(self) -> str:
        return f"{self.task}:{self.rule}"


# ── statistics (no deps) ──────────────────────────────────────────────────────

_Z = {0.95: 1.6448536269514722, 0.99: 2.3263478740408408}  # one-sided z


def _wilson_bounds(k: int, n: int, z: float) -> tuple[float, float]:
    """Wilson score interval (lo, hi) for a proportion k/n at z."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _two_proportion_lower_bound(k_rec: int, n_rec: int, k_base: int, n_base: int, conf: float) -> float:
    """One-sided lower bound of (p_rec - p_base) via Newcombe's method (combines
    the two Wilson intervals). >0 means recent is significantly worse."""
    z = _Z[conf]
    rec_lo, _ = _wilson_bounds(k_rec, n_rec, z)
    _, base_hi = _wilson_bounds(k_base, n_base, z)
    return rec_lo - base_hi


# ── per-task metric extraction from grouped rows ──────────────────────────────
# A "rows" input is a dict: task -> {
#   "calls": int, "persistent_fails": int, "transient_fails": int,
#   "ok_calls": int, "p50_latency_ms": float|None, "avg_tokens": float|None,
#   "dominant_model": str, "days_span": int
# }  (the IO layer builds this with SQL; tests pass it directly)

def _task_set(recent: dict, baseline: dict) -> list[str]:
    return sorted(set(recent) | set(baseline))


def evaluate_drift(
    recent_rows: dict[str, dict],
    baseline_rows: dict[str, dict],
    oldest_row_age_days: float,
    prev_state: dict[str, dict],
    now: datetime,
) -> list[Finding]:
    """PURE drift evaluation. No DB/network.

    `prev_state`: finding_key -> {"severity": str, "first_seen_at": datetime,
    "last_posted_at": datetime|None}. Used for persistence + dedup decisions.
    Returns Findings with `should_post` already decided (respects cold-start, the
    armed flag, persistence, and the 7-day dedup cooldown).

    Contract: detects a >=10pp persistent-error-rate lift (n_rec>=30, base>=100).
    Does NOT detect drift in sparse tasks, nor token/cost drift (digest-only)."""
    T = THRESHOLDS
    findings: list[Finding] = []
    armed = _is_armed()
    cold = oldest_row_age_days < T["COLD_START_DAYS"]

    for task in _task_set(recent_rows, baseline_rows):
        rec = recent_rows.get(task, {})
        base = baseline_rows.get(task, {})
        n_rec = int(rec.get("calls", 0))
        n_base = int(base.get("calls", 0))
        k_rec = int(rec.get("persistent_fails", 0))
        k_base = int(base.get("persistent_fails", 0))
        trans_rec = int(rec.get("transient_fails", 0))

        # ── outage-dedup guard: a transient-dominated week is an outage, not drift
        if n_rec >= T["ER_MIN_RECENT_CALLS"] and trans_rec / n_rec >= T["OUTAGE_TRANSIENT_RATE"]:
            findings.append(Finding(
                task, "OUTAGE_SUPPRESSED", "INFO", "digest",
                f"{task}: ช่วงนี้ error ส่วนใหญ่เป็นแบบชั่วคราว (provider/เครือข่าย) — "
                f"ดูแลโดย /health/deep + Uptime Robot ไม่ใช่ drift",
                observed=round(trans_rec / n_rec, 3),
            ))
            continue  # suppress all other findings for this task

        # ── model change → INFO + suppress latency/token for this task
        rec_model = rec.get("dominant_model")
        base_model = base.get("dominant_model")
        model_changed = bool(rec_model and base_model and rec_model != base_model)
        if model_changed:
            findings.append(Finding(
                task, "MODEL_CHANGE", "INFO", "digest",
                f"{task}: โมเดลเปลี่ยน {base_model} → {rec_model} — baseline กำลังรีเซ็ต "
                f"(latency/token ของ task นี้ถูกพักการเตือนชั่วคราว)",
            ))

        # ── ERROR_RATE_REGRESSION (the only ratio-based ping) ─────────────────
        gate_ok = (n_rec >= T["ER_MIN_RECENT_CALLS"] and k_rec >= T["ER_MIN_RECENT_FAILS"]
                   and n_base >= T["ER_MIN_BASELINE_CALLS"])
        if gate_ok:
            p_rec = k_rec / n_rec
            p_base = k_base / n_base
            lift = p_rec - p_base
            if k_base == 0:
                # rule of three: need a clear floor over a zero-error baseline
                rule_of_three = max(T["ER_MIN_ABS_LIFT"], 3.0 / n_base)
                if p_rec >= rule_of_three:
                    findings.append(_mk_error_finding(task, p_rec, p_base, "CRIT", prev_state, now, armed, cold))
            elif lift >= T["ER_MIN_ABS_LIFT"]:
                lb95 = _two_proportion_lower_bound(k_rec, n_rec, k_base, n_base, T["ER_WARN_CONF"])
                lb99 = _two_proportion_lower_bound(k_rec, n_rec, k_base, n_base, T["ER_CRIT_CONF"])
                if lb99 > 0:
                    sev = _escalation_severity(f"{task}:ERROR_RATE_REGRESSION", prev_state, now)
                    findings.append(_mk_error_finding(task, p_rec, p_base, sev, prev_state, now, armed, cold))
                elif lb95 > 0:
                    findings.append(_mk_error_finding(task, p_rec, p_base, "WARN", prev_state, now, armed, cold))

        # ── ERROR_FLOOR (baseline-independent) ────────────────────────────────
        if (n_rec >= T["FLOOR_MIN_RECENT_CALLS"] and k_rec >= T["FLOOR_MIN_RECENT_FAILS"]
                and (k_rec / n_rec) >= T["FLOOR_RATE"]):
            p_rec = k_rec / n_rec
            f = Finding(
                task, "ERROR_FLOOR", "CRIT", "ping",
                f"⚠️ {task}: error จริง {p_rec*100:.0f}% ของ {n_rec} ครั้งใน 7 วัน — "
                f"ฟีเจอร์นี้น่าจะพัง (key หมด? prompt/สคีมาเปลี่ยน?) โปรดตรวจ /ai/stats + /ai/calls",
                observed=round(p_rec, 3),
            )
            f.should_post = armed and not cold and _dedup_allows(f, prev_state, now)
            findings.append(f)

        # ── LATENCY_DRIFT (digest only, suppressed on model change) ───────────
        if not model_changed:
            lr = rec.get("p50_latency_ms"); lb = base.get("p50_latency_ms")
            if (lr and lb and int(rec.get("ok_calls", 0)) >= T["LAT_MIN_RECENT"]
                    and lr >= T["LAT_RATIO"] * lb and (lr - lb) >= T["LAT_MIN_ABS_MS"]):
                findings.append(Finding(
                    task, "LATENCY_DRIFT", "INFO", "digest",
                    f"{task}: latency p50 {lb:.0f}→{lr:.0f}ms ช้าลง",
                    observed=round(lr, 0), baseline=round(lb, 0),
                ))

            # ── TOKEN_PROFILE (digest only) ───────────────────────────────────
            tr = rec.get("avg_tokens"); tb = base.get("avg_tokens")
            if tr and tb and int(base.get("ok_calls", 0)) >= T["TOKEN_MIN_BASELINE"] and tr >= T["TOKEN_RATIO"] * tb:
                findings.append(Finding(
                    task, "TOKEN_PROFILE", "INFO", "digest",
                    f"{task}: token เฉลี่ย/ครั้ง {tb:.0f}→{tr:.0f} (อาจมาจากบิลยาวขึ้น ไม่จำเป็นต้องเป็น drift)",
                    observed=round(tr, 0), baseline=round(tb, 0),
                ))

        # ── VOLUME_COLLAPSE (digest only) ─────────────────────────────────────
        base_days = max(int(base.get("days_span", T["BASELINE_DAYS"])), 1)
        base_per_day = n_base / base_days
        if base_per_day >= T["VOL_MIN_BASELINE_PER_DAY"] and n_rec == 0:
            findings.append(Finding(
                task, "VOLUME_COLLAPSE", "INFO", "digest",
                f"{task}: เคยเรียก ~{base_per_day:.0f}/วัน แต่ 7 วันนี้เงียบ (0 ครั้ง) — ฟีเจอร์ถูกปิดไว้?",
                baseline=round(base_per_day, 1),
            ))

    return findings


def _escalation_severity(key: str, prev_state: dict, now: datetime) -> str:
    """ERROR_RATE_REGRESSION escalates to CRIT only after persisting across 2 runs."""
    prev = prev_state.get(key)
    if prev and prev.get("first_seen_at") and prev["first_seen_at"] < now:
        return "CRIT"
    return "WARN"  # first sighting → digest only, wait for confirmation


def _mk_error_finding(task, p_rec, p_base, severity, prev_state, now, armed, cold) -> Finding:
    chan = "ping" if severity == "CRIT" else "digest"
    f = Finding(
        task, "ERROR_RATE_REGRESSION", severity, chan,
        f"⚠️ {task}: error จริงเพิ่มจาก {p_base*100:.0f}% → {p_rec*100:.0f}% (เทียบ baseline 21 วัน) — "
        f"โปรดตรวจ key/prompt ของฟีเจอร์นี้ที่ /ai/stats + /ai/calls",
        observed=round(p_rec, 3), baseline=round(p_base, 3),
    )
    if chan == "ping":
        f.should_post = armed and not cold and _dedup_allows(f, prev_state, now)
    return f


def _dedup_allows(f: Finding, prev_state: dict, now: datetime) -> bool:
    """Post only if NEW, ESCALATED in severity, or last_posted older than cooldown."""
    prev = prev_state.get(f.key)
    if prev is None:
        return True
    sev_rank = {"INFO": 0, "WARN": 1, "CRIT": 2}
    if sev_rank.get(f.severity, 0) > sev_rank.get(prev.get("severity", "INFO"), 0):
        return True
    last = prev.get("last_posted_at")
    if last is None:
        return True
    return (now - last) >= timedelta(days=THRESHOLDS["DEDUP_COOLDOWN_DAYS"])


# ── renderers ─────────────────────────────────────────────────────────────────

def render_discord_message(findings: list[Finding]) -> Optional[str]:
    """Build the single Discord message from findings flagged should_post.
    Returns None when there is nothing to post (clean day = silence)."""
    posts = [f for f in findings if f.should_post]
    if not posts:
        return None
    lines = ["🤖 AI Drift Alert — ตรวจพบความผิดปกติของ AI", "─" * 26]
    for f in posts:
        lines.append(f.message_th)
    lines.append("─" * 26)
    lines.append("เงียบ = ปกติ · ข้อความนี้ = ควรเช็ก /ai/stats")
    return "\n".join(lines)[:1900]


def render_digest_line(findings: list[Finding]) -> str:
    """One line for the Monday weekly_summary."""
    notable = [f for f in findings if f.rule not in ("OUTAGE_SUPPRESSED",)]
    if not notable:
        return "AI สัปดาห์นี้: ปกติ ✅"
    crit = sum(1 for f in notable if f.severity == "CRIT")
    return (f"AI สัปดาห์นี้: มี {len(notable)} ข้อสังเกต"
            + (f" ({crit} ร้ายแรง)" if crit else "")
            + " — ดู /ai/drift")


# ── IO wrapper (not pure; thin) ───────────────────────────────────────────────

def _get_conn():
    try:
        from main import get_db_conn  # type: ignore
        return get_db_conn()
    except Exception:
        import psycopg2
        return psycopg2.connect(os.environ["DATABASE_URL"])


def _window_rows(cur, start, end) -> dict[str, dict]:
    """Aggregate ai_call_log per task over [start, end). Persistent vs transient
    split + p50 latency + avg tokens + dominant model."""
    cur.execute(
        """
        SELECT task,
               COUNT(*)                                                          AS calls,
               COUNT(*) FILTER (WHERE NOT ok AND status = ANY(%s))               AS persistent_fails,
               COUNT(*) FILTER (WHERE NOT ok AND (status = ANY(%s) OR status IS NULL)) AS transient_fails,
               COUNT(*) FILTER (WHERE ok)                                        AS ok_calls,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms)
                   FILTER (WHERE ok AND latency_ms IS NOT NULL)                  AS p50_latency_ms,
               AVG(total_tokens) FILTER (WHERE ok AND total_tokens IS NOT NULL)  AS avg_tokens,
               MODE() WITHIN GROUP (ORDER BY model)                             AS dominant_model
        FROM public.ai_call_log
        WHERE created_at >= %s AND created_at < %s
        GROUP BY task
        """,
        (list(PERSISTENT_STATUSES), list(TRANSIENT_STATUSES), start, end),
    )
    out: dict[str, dict] = {}
    for r in cur.fetchall():
        out[r[0]] = {
            "calls": int(r[1]), "persistent_fails": int(r[2]), "transient_fails": int(r[3]),
            "ok_calls": int(r[4]),
            "p50_latency_ms": float(r[5]) if r[5] is not None else None,
            "avg_tokens": float(r[6]) if r[6] is not None else None,
            "dominant_model": r[7],
        }
    return out


def run_drift_check(dry_run: bool = True, post: bool = False) -> dict:
    """Read ai_call_log, evaluate drift, optionally post + persist state.
    `dry_run=True` (default): compute only, never post, never mutate ai_drift_state.
    `post=True`: real run (writes state, posts armed pings). Returns a JSON-able report.
    Raises on a DB/logic error (so @_heartbeat records the failure); Discord +
    state-write are best-effort inside their own try/except."""
    now = datetime.now(timezone.utc)
    T = THRESHOLDS
    # Day-aligned Bangkok windows: compute on fully-closed days.
    bkk_now = now + timedelta(hours=7)
    today_bkk = bkk_now.date()
    recent_end = datetime(today_bkk.year, today_bkk.month, today_bkk.day, tzinfo=timezone.utc) - timedelta(hours=7)
    recent_start = recent_end - timedelta(days=T["RECENT_DAYS"])
    base_end = recent_start
    base_start = base_end - timedelta(days=T["BASELINE_DAYS"])

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT MIN(created_at) FROM public.ai_call_log")
            oldest = cur.fetchone()[0]
            oldest_age_days = (now - oldest).total_seconds() / 86400 if oldest else 0.0

            recent_rows = _window_rows(cur, recent_start, recent_end)
            baseline_rows = _window_rows(cur, base_start, base_end)
            for d in baseline_rows.values():
                d["days_span"] = T["BASELINE_DAYS"]

            prev_state = _read_state(cur)
    finally:
        conn.close()

    findings = evaluate_drift(recent_rows, baseline_rows, oldest_age_days, prev_state, now)
    msg = render_discord_message(findings)
    digest = render_digest_line(findings)
    warming_up = oldest_age_days < T["COLD_START_DAYS"]
    paging_from = (oldest + timedelta(days=T["COLD_START_DAYS"])).date().isoformat() if oldest else None

    report = {
        "warming_up": warming_up,
        "paging_from_date": paging_from,
        "armed": _is_armed(),
        "oldest_row_age_days": round(oldest_age_days, 1),
        "findings": [_finding_dict(f) for f in findings],
        "would_post": [_finding_dict(f) for f in findings if f.should_post],
        "rendered_discord_message": msg,
        "digest_line": digest,
    }

    if dry_run or not post:
        return report

    # Real run: post (best-effort) + persist state (best-effort).
    if msg:
        try:
            import auto_diagnose
            auto_diagnose._post_to_discord(msg)
        except Exception:
            log.warning("drift_monitor: Discord post failed (non-fatal)")
    try:
        _write_state(findings, now)
    except Exception:
        log.warning("drift_monitor: ai_drift_state write failed (non-fatal)")
    return report


def _finding_dict(f: Finding) -> dict:
    return {"task": f.task, "rule": f.rule, "severity": f.severity, "channel": f.channel,
            "message": f.message_th, "observed": f.observed, "baseline": f.baseline,
            "should_post": f.should_post}


def _read_state(cur) -> dict[str, dict]:
    try:
        cur.execute("SELECT finding_key, severity, first_seen_at, last_posted_at FROM public.ai_drift_state")
        return {r[0]: {"severity": r[1], "first_seen_at": r[2], "last_posted_at": r[3]} for r in cur.fetchall()}
    except Exception:
        log.warning("drift_monitor: ai_drift_state read failed — treating as empty")
        return {}


def _write_state(findings: list[Finding], now: datetime) -> None:
    """Upsert seen findings; mark last_posted_at for posted ones. (Resolution of
    cleared keys is left to a future tidy; stale rows are harmless.)"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            for f in findings:
                cur.execute(
                    """
                    INSERT INTO public.ai_drift_state
                        (finding_key, severity, first_seen_at, last_posted_at, last_value, updated_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (finding_key) DO UPDATE
                    SET severity = EXCLUDED.severity,
                        last_posted_at = CASE WHEN %s THEN EXCLUDED.last_posted_at
                                              ELSE public.ai_drift_state.last_posted_at END,
                        last_value = EXCLUDED.last_value,
                        updated_at = NOW()
                    """,
                    (f.key, f.severity, now, (now if f.should_post else None), f.observed, f.should_post),
                )
        conn.commit()
    finally:
        conn.close()
