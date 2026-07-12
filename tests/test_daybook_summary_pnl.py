from datetime import date
from decimal import Decimal

import phase3_daybook_routes as daybook
from phase3_daybook_routes import _pnl_totals_from_row


def test_pnl_totals_keep_income_and_expense_separate():
    income_pnl, expense_pnl, net_pnl = _pnl_totals_from_row(
        (225_924.63, 201_929.67)
    )

    assert income_pnl == 225_924.63
    assert expense_pnl == 201_929.67
    assert net_pnl == 23_994.96


def test_daybook_summary_returns_explicit_pnl_fields(monkeypatch):
    class FakeCursor:
        def __init__(self):
            self.queries = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, _params):
            self.queries.append(query)

        def fetchall(self):
            if len(self.queries) == 1:
                return [
                    ("income", 12, Decimal("427823.87")),
                    ("expense", 8, Decimal("203302.31")),
                ]
            return []

        def fetchone(self):
            assert "AS income_pnl" in self.queries[-1]
            assert "AS expense_pnl" in self.queries[-1]
            return Decimal("225924.63"), Decimal("201929.67")

    class FakeConnection:
        def __init__(self):
            self.cursor_instance = FakeCursor()

        def cursor(self):
            return self.cursor_instance

        def close(self):
            pass

    monkeypatch.setattr(daybook, "get_db_conn", FakeConnection)

    result = daybook.daybook_summary(
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 30),
        source=None,
    )

    assert result["income_pnl"] == 225_924.63
    assert result["expense_pnl"] == 201_929.67
    assert result["net_pnl"] == 23_994.96
    assert result["net"] == 224_521.56
