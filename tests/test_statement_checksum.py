"""Unit test for the bank-statement import checksum (audit AUD-DATA-01).

Pure logic — monkeypatches the PDF summary read so it needs no real PDF / DB / key.
Run: pytest tests/test_statement_checksum.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")

import phase12_bank_statement_routes as m


def _rows(deposits, withdrawals):
    return (
        [{"credit": v, "debit": 0.0} for v in deposits]
        + [{"credit": 0.0, "debit": v} for v in withdrawals]
    )


def test_checksum_match(monkeypatch):
    monkeypatch.setattr(m, "_read_pdf_summary_totals",
                        lambda b: {"dep_n": 2, "dep_sum": 300.0, "wd_n": 1, "wd_sum": 50.0})
    r = m._statement_checksum(b"x", _rows([100.0, 200.0], [50.0]))
    assert r["available"] is True
    assert r["ok"] is True
    assert r["deposits"]["drift_sum"] == 0.0
    assert r["withdrawals"]["drift_sum"] == 0.0


def test_checksum_deposit_drift(monkeypatch):
    # statement says 300 deposits but we only parsed 250 -> drift -50, ok=False
    monkeypatch.setattr(m, "_read_pdf_summary_totals",
                        lambda b: {"dep_n": 2, "dep_sum": 300.0, "wd_n": 1, "wd_sum": 50.0})
    r = m._statement_checksum(b"x", _rows([100.0, 150.0], [50.0]))
    assert r["ok"] is False
    assert abs(r["deposits"]["drift_sum"] - (-50.0)) < 0.01


def test_checksum_count_drift(monkeypatch):
    # sums match but a row was dropped (count differs) -> ok=False
    monkeypatch.setattr(m, "_read_pdf_summary_totals",
                        lambda b: {"dep_n": 3, "dep_sum": 300.0, "wd_n": 0, "wd_sum": 0.0})
    r = m._statement_checksum(b"x", _rows([150.0, 150.0], []))
    assert r["ok"] is False


def test_checksum_no_summary_line(monkeypatch):
    monkeypatch.setattr(m, "_read_pdf_summary_totals",
                        lambda b: {"dep_n": None, "dep_sum": None, "wd_n": None, "wd_sum": None})
    r = m._statement_checksum(b"x", _rows([100.0], []))
    assert r["available"] is False


def test_checksum_read_raises_is_safe(monkeypatch):
    def boom(b):
        raise RuntimeError("pdf broke")
    monkeypatch.setattr(m, "_read_pdf_summary_totals", boom)
    r = m._statement_checksum(b"x", _rows([100.0], []))
    assert r == {"ok": None, "available": False}
