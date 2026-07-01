"""Loan (เงินยืม) ledger endpoints.

Read-only views over bank_statement_entries rows tagged source_type
loan_in / loan_repayment. A loan is a financing/liability item, excluded
from the P&L (see migrations/2026_05_31_loan_sources_pnl_exclude.sql).
Lender name comes from the `notes` column, set when the row is tagged via
POST /classify/{entry_id} (phase12_bank_statement_routes.py).
"""
import logging
import os

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException

logger = logging.getLogger("loan")
router = APIRouter(prefix="/loans", tags=["loans"])


def _get_db_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


@router.get("")
def list_loans():
    """Per-lender outstanding balance (borrowed - repaid)."""
    conn = _get_db_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT lender, borrowed, repaid, outstanding,
                       last_activity, txn_count
                FROM public.v_loan_balance
                ORDER BY outstanding DESC
                """
            )
            rows = cur.fetchall()
        for r in rows:
            for f in ("borrowed", "repaid", "outstanding"):
                r[f] = float(r[f] or 0)
            r["txn_count"] = int(r["txn_count"] or 0)
            r["last_activity"] = str(r["last_activity"]) if r["last_activity"] else None
        return {"lenders": rows}
    except Exception as e:
        logger.exception("list_loans failed")
        raise HTTPException(500, f"โหลดยอดเงินยืมไม่สำเร็จ: {e}")
    finally:
        conn.close()


@router.get("/{lender}")
def loan_detail(lender: str):
    """Per-lender transaction list (each loan_in / loan_repayment row)."""
    conn = _get_db_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id::text, txn_date, direction,
                       COALESCE(loan_amount_override, amount) AS amount,
                       source_type, description
                FROM public.bank_statement_entries
                WHERE COALESCE(NULLIF(btrim(notes), ''), 'ไม่ระบุผู้ให้ยืม') = %s
                  AND source_type IN ('loan_in', 'loan_repayment')
                ORDER BY txn_date
                """,
                (lender,),
            )
            rows = cur.fetchall()
        for r in rows:
            r["amount"] = float(r["amount"] or 0)
            r["txn_date"] = str(r["txn_date"]) if r["txn_date"] else None
        return {"lender": lender, "entries": rows}
    except Exception as e:
        logger.exception("loan_detail failed lender=%s", lender)
        raise HTTPException(500, f"โหลดรายการเงินยืมไม่สำเร็จ: {e}")
    finally:
        conn.close()
