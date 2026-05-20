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
from typing import Callable, TypeVar

import psycopg2
from fastapi import APIRouter

log = logging.getLogger("cron_heartbeat")
router = APIRouter(prefix="/cron", tags=["cron"])

F = TypeVar("F", bound=Callable[..., object])


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


def record_heartbeat(job_id: str, ok: bool, error_message: str | None = None) -> None:
    """Insert/update a heartbeat row for a job. Best-effort — failures
    are swallowed so heartbeat instrumentation never breaks a job."""
    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                if ok:
                    cur.execute(
                        """
                        INSERT INTO public.job_heartbeat
                            (job_id, last_run_at, last_success_at, run_count)
                        VALUES (%s, NOW(), NOW(), 1)
                        ON CONFLICT (job_id) DO UPDATE
                        SET last_run_at      = NOW(),
                            last_success_at  = NOW(),
                            run_count        = job_heartbeat.run_count + 1,
                            updated_at       = NOW()
                        """,
                        (job_id,),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO public.job_heartbeat
                            (job_id, last_run_at, last_error_at,
                             last_error_message, run_count, error_count)
                        VALUES (%s, NOW(), NOW(), %s, 1, 1)
                        ON CONFLICT (job_id) DO UPDATE
                        SET last_run_at         = NOW(),
                            last_error_at       = NOW(),
                            last_error_message  = EXCLUDED.last_error_message,
                            run_count           = job_heartbeat.run_count + 1,
                            error_count         = job_heartbeat.error_count + 1,
                            updated_at          = NOW()
                        """,
                        (job_id, (error_message or "")[:500]),
                    )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        log.exception("record_heartbeat failed (job_id=%s, ok=%s)", job_id, ok)


def heartbeat(job_id: str) -> Callable[[F], F]:
    """Decorator that records a heartbeat around the wrapped function."""
    def deco(fn: F) -> F:
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
                record_heartbeat(job_id, ok=True)
                return result
            except Exception as e:
                record_heartbeat(job_id, ok=False, error_message=str(e))
                raise
        return wrapped  # type: ignore[return-value]
    return deco


# ─────────────────────────────────────────────
# /cron/health endpoint
# ─────────────────────────────────────────────

@router.get("/health")
def cron_health():
    """Return per-job heartbeat state. Flags jobs whose last_run_at
    is more than 2× expected_interval_hours ago as 'stale'.

    Returns 200 (healthy) or 503 (any stale) so Uptime Robot can poll
    this directly and alert without parsing the body.
    """
    from fastapi.responses import JSONResponse

    try:
        conn = _get_conn()
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "db_unreachable", "error": str(e)[:200]},
        )

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

    body = {
        "status": "stale" if any_stale else "healthy",
        "jobs": jobs,
    }
    return JSONResponse(status_code=503 if any_stale else 200, content=body)
