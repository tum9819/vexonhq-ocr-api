"""
ai_monitor_routes.py — AI telemetry dashboard endpoints.

Monitoring-phase remediation from the 2026-05-31 AI Life-Cycle audit. Reads the
public.ai_call_log table (written best-effort by llm.py) and reports per-feature
token usage, estimated cost (฿), latency, and error-rate so AI quality, model
drift, and spend become visible.

Both endpoints are JWT-gated (NOT in PUBLIC_PATHS) — the log records prompts/usage
and must not be public. Read-only.
"""

from __future__ import annotations

import logging
import os

import psycopg2
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

log = logging.getLogger("ai_monitor")
router = APIRouter(prefix="/ai", tags=["ai-monitor"])


def _get_conn():
    """DB connection. Mirrors cron_heartbeat._get_conn — prefer main.get_db_conn,
    fall back to a direct psycopg2 connection in standalone/test contexts."""
    try:
        from main import get_db_conn  # type: ignore
        return get_db_conn()
    except Exception:
        return psycopg2.connect(os.environ["DATABASE_URL"])


@router.get("/stats")
def ai_stats(days: int = Query(30, ge=1, le=365)):
    """Per-task aggregate over the last ``days``: call count, ok/error counts,
    error-rate, token totals, and an ESTIMATED cost in ฿. Plus a per-day series
    for drift-spotting. Returns 200 with empty lists if the table is empty."""
    from llm import estimate_cost_thb  # local import — avoids import cycle at load

    try:
        conn = _get_conn()
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=503,
                            content={"status": "db_unreachable", "error": str(e)[:200]})
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT task, model,
                       COUNT(*)                                  AS calls,
                       COUNT(*) FILTER (WHERE ok)                AS ok_calls,
                       COUNT(*) FILTER (WHERE NOT ok)            AS error_calls,
                       COALESCE(SUM(prompt_tokens), 0)           AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0)       AS completion_tokens,
                       COALESCE(SUM(total_tokens), 0)            AS total_tokens,
                       COALESCE(ROUND(AVG(latency_ms)), 0)       AS avg_latency_ms
                FROM public.ai_call_log
                WHERE created_at >= NOW() - (%s || ' days')::interval
                GROUP BY task, model
                ORDER BY calls DESC
                """,
                (days,),
            )
            rows = cur.fetchall()

            cur.execute(
                """
                SELECT date_trunc('day', created_at)::date AS day,
                       COUNT(*)                       AS calls,
                       COUNT(*) FILTER (WHERE NOT ok) AS error_calls,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens
                FROM public.ai_call_log
                WHERE created_at >= NOW() - (%s || ' days')::interval
                GROUP BY 1 ORDER BY 1
                """,
                (days,),
            )
            day_rows = cur.fetchall()
    finally:
        conn.close()

    by_task = []
    grand_calls = grand_errors = grand_tokens = 0
    grand_cost = 0.0
    for (task, model, calls, ok_calls, error_calls, ptok, ctok, ttok, avg_lat) in rows:
        cost = estimate_cost_thb(model, int(ptok), int(ctok))
        grand_calls += int(calls)
        grand_errors += int(error_calls)
        grand_tokens += int(ttok)
        grand_cost += cost
        by_task.append({
            "task": task, "model": model,
            "calls": int(calls), "ok": int(ok_calls), "errors": int(error_calls),
            "error_rate": round(int(error_calls) / int(calls), 4) if calls else 0.0,
            "prompt_tokens": int(ptok), "completion_tokens": int(ctok),
            "total_tokens": int(ttok), "avg_latency_ms": int(avg_lat),
            "est_cost_thb": round(cost, 2),
        })

    daily = [
        {"day": d.isoformat(), "calls": int(c), "errors": int(e), "total_tokens": int(t)}
        for (d, c, e, t) in day_rows
    ]

    return {
        "window_days": days,
        "totals": {
            "calls": grand_calls,
            "errors": grand_errors,
            "error_rate": round(grand_errors / grand_calls, 4) if grand_calls else 0.0,
            "total_tokens": grand_tokens,
            "est_cost_thb": round(grand_cost, 2),
        },
        "by_task": by_task,
        "daily": daily,
        "cost_note": "ต้นทุนเป็นค่าประมาณจาก list price (override ด้วย env AI_PRICES_JSON / USD_THB)",
    }


@router.get("/calls")
def ai_calls(limit: int = Query(50, ge=1, le=500)):
    """Most recent AI calls for spot-checking (task/model/ok/tokens/latency/error)."""
    try:
        conn = _get_conn()
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=503,
                            content={"status": "db_unreachable", "error": str(e)[:200]})
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT created_at, provider, task, model, ok,
                       prompt_tokens, completion_tokens, total_tokens,
                       latency_ms, status, error
                FROM public.ai_call_log
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    calls = [
        {
            "created_at": created_at.isoformat() if created_at else None,
            "provider": provider, "task": task, "model": model, "ok": ok,
            "prompt_tokens": ptok, "completion_tokens": ctok, "total_tokens": ttok,
            "latency_ms": lat, "status": status, "error": error,
        }
        for (created_at, provider, task, model, ok, ptok, ctok, ttok, lat, status, error) in rows
    ]
    return {"count": len(calls), "calls": calls}
