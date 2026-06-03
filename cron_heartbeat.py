"""
VEXONHQ Stability Initiative — P1.2 Cron heartbeat tracking
============================================================

Each scheduled job wraps its work with `record_heartbeat()` to write a
row in public.job_heartbeat. The /cron/health endpoint reads that table
and reports stale jobs (last_run > 2× expected_interval_hours).

USAGE
-----
At the top of a scheduled function:

    from cron_heartbeat import heartbeat
    @heartbeat("daily_line_digest")
    def _scheduled_daily_digest():
        ...

The decorator updates last_run_at + last_success_at + run_count on
clean exit, last_error_at + last_error_message + error_count on
exception (then re-raises so APScheduler can log it).

/cron/health response shape:
    {
      "status": "healthy" | "stale",
      "jobs": [
        {"job_id": "...", "last_run_at": iso, "stale": bool,
         "minutes_since_last_run": int, "expected_interval_hours": int,
         "last_error_at": iso|null, "error_count": int}
      ]
    }
"""

from __future__ import annotations

import functools
import logging
import os
import time
from typing import Callable, TypeVar

import psycopg2
from fastapi import APIRouter

log = logging.getLogger("cron_heartbeat")
router = APIRouter(prefix="/cron", tags=["cron"])

F = TypeVar("F", bound=Callable[..., object])

# Every @heartbeat-decorated job_id registers here at import time, so /cron/health
# can flag a job that has NEVER written a heartbeat row (dead-on-arrival) — which a
# table-only read would miss entirely (audit CEO-REL-01).
_REGISTERED_JOBS: set[str] = set()


def _get_conn():
    """Open a Postgres connection. Mirrors main.get_db_conn but falls
    back to direct psycopg2 if main isn't import-safe (e.g. test
    contexts, or transient import-time errors in a sibling module).
    Catches *any* exception during the import attempt — not just
    ImportError — because partially-loaded modules can raise typing /
    attribute errors that we still want to recover from."""
    try:
        from main import get_db_conn  # type: ignore
        return get_db_conn()
    except Exception:
        return psycopg2.connect(os.environ["DATABASE_URL"])


def record_heartbeat(
    job_id: str,
    ok: bool,
    error_message: str | None = None,
    expected_interval_hours: int = 24,
) -> None:
    """Insert/update a heartbeat row for a job. Best-effort — failures
    are swallowed so heartbeat instrumentation never breaks a job.

    expected_interval_hours: how often this job is scheduled (24 for daily,
    168 for weekly). Used by /cron/health to flag stale jobs (> 2× interval).
    Always written on every run so the value stays correct after code changes.
    """
    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                if ok:
                    cur.execute(
                        """
                        INSERT INTO public.job_heartbeat
                            (job_id, last_run_at, last_success_at, run_count,
                             expected_interval_hours)
                        VALUES (%s, NOW(), NOW(), 1, %s)
                        ON CONFLICT (job_id) DO UPDATE
                        SET last_run_at              = NOW(),
                            last_success_at          = NOW(),
                            run_count                = job_heartbeat.run_count + 1,
                            expected_interval_hours  = EXCLUDED.expected_interval_hours,
                            updated_at               = NOW()
                        """,
                        (job_id, expected_interval_hours),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO public.job_heartbeat
                            (job_id, last_run_at, last_error_at,
                             last_error_message, run_count, error_count,
                             expected_interval_hours)
                        VALUES (%s, NOW(), NOW(), %s, 1, 1, %s)
                        ON CONFLICT (job_id) DO UPDATE
                        SET last_run_at              = NOW(),
                            last_error_at            = NOW(),
                            last_error_message       = EXCLUDED.last_error_message,
                            run_count                = job_heartbeat.run_count + 1,
                            error_count              = job_heartbeat.error_count + 1,
                            expected_interval_hours  = EXCLUDED.expected_interval_hours,
                            updated_at               = NOW()
                        """,
                        (job_id, (error_message or "")[:500], expected_interval_hours),
                    )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        log.exception("record_heartbeat failed (job_id=%s, ok=%s)", job_id, ok)


def heartbeat(job_id: str, expected_interval_hours: int = 24) -> Callable[[F], F]:
    """Decorator that records a heartbeat around the wrapped function.

    expected_interval_hours: how often this job runs (24=daily, 168=weekly).
    /cron/health flags a job stale when last_run > 2× this value.
    """
    _REGISTERED_JOBS.add(job_id)  # track expected jobs for missing-job detection (CEO-REL-01)
    def deco(fn: F) -> F:
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
                record_heartbeat(job_id, ok=True,
                                 expected_interval_hours=expected_interval_hours)
                return result
            except Exception as e:
                record_heartbeat(job_id, ok=False, error_message=str(e),
                                 expected_interval_hours=expected_interval_hours)
                raise
        return wrapped  # type: ignore[return-value]
    return deco


# ─────────────────────────────────────────────
# /cron/health endpoint
# ─────────────────────────────────────────────

def _compute_job_states() -> tuple[list[dict], list[str], bool]:
    """Read public.job_heartbeat and compute per-job stale state + the set of
    registered-but-never-written (missing) jobs.

    Returns (jobs, missing_jobs, any_stale). Raises on DB error. Shared by
    /cron/health (passive) and the active stale-job watchdog so both judge
    staleness identically.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    job_id,
                    last_run_at,
                    last_success_at,
                    last_error_at,
                    last_error_message,
                    expected_interval_hours,
                    run_count,
                    error_count,
                    EXTRACT(EPOCH FROM (NOW() - last_run_at))::bigint AS seconds_since_last_run
                FROM public.job_heartbeat
                ORDER BY job_id
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    jobs = []
    any_stale = False
    for row in rows:
        (
            job_id, last_run_at, last_success_at, last_error_at,
            last_error_message, expected_interval_hours, run_count,
            error_count, seconds_since,
        ) = row

        stale_threshold_seconds = expected_interval_hours * 3600 * 2  # 2× interval
        is_stale = bool(seconds_since and seconds_since > stale_threshold_seconds)
        if is_stale:
            any_stale = True

        jobs.append({
            "job_id": job_id,
            "last_run_at": last_run_at.isoformat() if last_run_at else None,
            "last_success_at": last_success_at.isoformat() if last_success_at else None,
            "last_error_at": last_error_at.isoformat() if last_error_at else None,
            "last_error_message": last_error_message,
            "expected_interval_hours": expected_interval_hours,
            "run_count": int(run_count or 0),
            "error_count": int(error_count or 0),
            "minutes_since_last_run": int((seconds_since or 0) // 60),
            "stale": is_stale,
        })

    # Jobs DECORATED with @heartbeat but never having written a row are invisible
    # to the table read above — surface them so a dead-on-arrival job can't hide
    # (audit CEO-REL-01).
    present_ids = {j["job_id"] for j in jobs}
    missing_jobs = sorted(_REGISTERED_JOBS - present_ids)
    return jobs, missing_jobs, any_stale


@router.api_route("/health", methods=["GET", "HEAD"], include_in_schema=False)
def cron_health():
    """Return per-job heartbeat state. Flags jobs whose last_run_at is more than
    2× expected_interval_hours ago as 'stale'.

    Returns 200 (healthy) or 503 (any stale) so Uptime Robot can poll this
    directly and alert without parsing the body. A registered job that has never
    written a row reports status "degraded" at HTTP 200 (so a freshly-deployed
    job awaiting its first run doesn't trip a false DOWN); stale jobs still 503.
    HEAD is accepted (Uptime Robot free plan only supports HEAD).
    """
    from fastapi.responses import JSONResponse

    try:
        jobs, missing_jobs, any_stale = _compute_job_states()
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "db_unreachable", "error": str(e)[:200]},
        )

    if any_stale:
        status = "stale"
    elif missing_jobs:
        status = "degraded"
    else:
        status = "healthy"
    return JSONResponse(
        status_code=503 if any_stale else 200,
        content={"status": status, "jobs": jobs, "missing_jobs": missing_jobs},
    )


# ─────────────────────────────────────────────
# OPS-11 — active stale-job watchdog
# ─────────────────────────────────────────────
# /cron/health is PASSIVE: Uptime Robot learns *that* something is stale (503)
# but not WHICH job (its free plan strips the body). This watchdog runs on a
# schedule, reads the same heartbeat state, and pushes the SPECIFIC stale/missing
# job_id(s) to Discord, rate-limited per job_id so it can't spam.
_STALE_ALERT_SECONDS = 6 * 3600          # re-alert a given job_id at most every 6h
_last_stale_alert_at: dict[str, float] = {}
# The watchdog writes its OWN heartbeat only AFTER it finishes, so on its first
# run it sees itself as "missing". Never alert about itself for the missing case
# (a self-reference, not a real dead job). Stale-detection still applies to it.
_SELF_JOB_ID = "cron_stale_watchdog"


def check_and_alert_stale_jobs(now: float | None = None) -> dict:
    """Active watchdog: read heartbeat state and post the SPECIFIC stale/missing
    job_id(s) to Discord (rate-limited per job_id). Best-effort — never raises
    (it runs inside APScheduler). `now` is injectable for testing."""
    try:
        jobs, missing, _any_stale = _compute_job_states()
    except Exception:
        log.exception("check_and_alert_stale_jobs: heartbeat read failed")
        return {"checked": False}

    stale = [j for j in jobs if j.get("stale")]
    _now = time.time() if now is None else now
    lines: list[str] = []
    alerted: list[str] = []

    for j in stale:
        jid = j["job_id"]
        if _now - _last_stale_alert_at.get(jid, 0) < _STALE_ALERT_SECONDS:
            continue
        _last_stale_alert_at[jid] = _now
        alerted.append(jid)
        mins = j.get("minutes_since_last_run", 0)
        lines.append(
            f"- `{jid}` STALE — last run {mins // 60}h{mins % 60}m ago "
            f"(expected every {j.get('expected_interval_hours')}h)"
        )
    for jid in missing:
        if jid == _SELF_JOB_ID:
            continue  # first-run self-reference, not a real dead job
        key = f"missing::{jid}"
        if _now - _last_stale_alert_at.get(key, 0) < _STALE_ALERT_SECONDS:
            continue
        _last_stale_alert_at[key] = _now
        alerted.append(jid)
        lines.append(f"- `{jid}` has NEVER run (no heartbeat row since deploy)")

    if not lines:
        return {"checked": True, "stale": [j["job_id"] for j in stale],
                "missing": missing, "alerted": []}
    try:
        from auto_diagnose import _post_to_discord
        _post_to_discord(
            "🕒 **Cron stale-job alert** — a scheduled job stopped firing:\n"
            + "\n".join(lines)
        )
    except Exception:
        log.exception("check_and_alert_stale_jobs: discord post failed")
    return {"checked": True, "stale": [j["job_id"] for j in stale],
            "missing": missing, "alerted": alerted}
