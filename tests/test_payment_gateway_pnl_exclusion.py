from pathlib import Path


REPO = Path(__file__).resolve().parents[1]

FILES_THAT_MUST_EXCLUDE_PAYMENT_GATEWAY_PAYOUT = [
    "pnl_routes.py",
    "phase2_routes.py",
    "cashflow_routes.py",
    "breakeven_routes.py",
    "phase10_narrative_routes.py",
    "line_bot_routes.py",
    "migrations/2026_05_27_v_daybook_pnl.sql",
    "migrations/2026_05_31_loan_sources_pnl_exclude.sql",
    "migrations/2026_07_06_payment_gateway_payout_pnl_exclude.sql",
]


def test_payment_gateway_payout_is_excluded_from_all_pnl_surfaces():
    missing = []
    for rel_path in FILES_THAT_MUST_EXCLUDE_PAYMENT_GATEWAY_PAYOUT:
        path = REPO / rel_path
        if not path.exists() or "payment_gateway_payout" not in path.read_text(encoding="utf-8"):
            missing.append(rel_path)

    assert missing == []
