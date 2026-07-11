import os

import psycopg2
import pytest

@pytest.fixture
def db_conn():
    database_url = os.environ.get("CATEGORY_INTEGRITY_DATABASE_URL")
    if not database_url:
        pytest.skip("set CATEGORY_INTEGRITY_DATABASE_URL for the read-only DB integrity check")

    conn = psycopg2.connect(database_url)
    conn.set_session(readonly=True, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def test_expense_categories_no_orphans(db_conn):
    """
    Ensure no P&L expense rows have a category_code missing from expense_categories.
    """
    whitelist = {"delivery_income", "reimbursement", "loan_in", "loan_repayment"}

    with db_conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT category_code
            FROM public.v_daybook_pnl
            WHERE direction='expense'
              AND category_code IS NOT NULL
        """)
        used_categories = {row[0] for row in cur.fetchall()}
        
        cur.execute("SELECT code FROM public.expense_categories")
        defined_categories = {row[0] for row in cur.fetchall()}
        
        orphans = used_categories - defined_categories - whitelist
        assert not orphans, f"Found orphaned expense categories in v_daybook_pnl: {orphans}"
