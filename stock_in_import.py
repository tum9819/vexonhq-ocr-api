"""
B2 M1 — FoodStory Stock-in Refill Importer
===========================================
Pure functions: key computation, row parser, reconcile diff.
DB-touching routes are in stock_in_routes.py.

Spec: VEXONHQ/docs/03_SPECS/B2_STOCKIN_AI_SEARCH_SPEC.md §§2-2.6b
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional

import pandas as pd


# ─── Header signature ────────────────────────────────────────────────────────
# Registered in pos_import.SIGNATURES as "stock_in_refill".
STOCK_IN_SIGNATURE: list[str] = [
    "วันที่",
    "ชื่อ",
    "รหัสวัตถุดิบ",
    "ป้ายกำกับ",
    "ประเภทการเติมวัตถุดิบ",
    "เติมสินค้า",
    "ค่าใช้จ่ายต่อหน่วย",
]

# ─── Column name map (FoodStory header → internal field name) ─────────────────
_COL_MAP: dict[str, str] = {
    "วันที่":                   "received_date_raw",
    "ชื่อ":                     "item_name",
    "รหัสวัตถุดิบ":              "material_code_raw",
    "ป้ายกำกับ":                 "tag",
    "ประเภทการเติมวัตถุดิบ":      "refill_type",
    "ประเภท":                   "refill_type",   # truncated header variant
    "เติมสินค้า":                "qty_raw",
    "ค่าใช้จ่ายต่อหน่วย":         "unit_cost_raw",
    "ค่าใช้จ่ายสุทธิ":            "net_cost_raw",
    "สาขา":                     "branch_code_raw",
    "หน่วย":                    "unit",
    "INVOICE":                  "invoice_no",
    "GR":                       "gr_ref",
    "PO":                       "po_ref",
    "วันที่ออก PO":              "po_date_raw",
}

# ─── Normalisation helpers ────────────────────────────────────────────────────

_WS_RE = re.compile(r"\s+")
_SEP = "\x00"   # null-byte field separator (cannot appear in content)


def _to_str(v, *, lowercase: bool = False) -> str:
    """Strip, NFC-normalise, collapse whitespace. Thai not lowercased by default."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = unicodedata.normalize("NFC", _WS_RE.sub(" ", str(v).strip()))
    return s.lower() if lowercase else s


def _norm_material_code(v) -> str:
    """'nan' / '' → '' (empty string used as ∅ in the hash)."""
    s = _to_str(v, lowercase=True)
    return "" if s in ("nan", "") else s


def _norm_decimal(v) -> str:
    """Canonical decimal string for a numeric field (used in hash)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "0"
    try:
        return str(Decimal(str(v)).normalize())
    except InvalidOperation:
        return "0"


def _norm_ref(v) -> str:
    """Invoice/GR/PO: trim + uppercase; None/blank/'nan'/'-' → ''."""
    s = _to_str(v).upper()
    return "" if s in ("NAN", "NONE", "-", "") else s


def _to_num(v) -> float:
    """Parse a numeric cell; None/blank → 0.0."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace(" ", "").replace("฿", "").strip()
    if not s or s == "-":
        return 0.0
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1].strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _to_date(v) -> Optional[date]:
    """Parse FoodStory date strings (DD/MM/YYYY) or pandas Timestamps."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


# ─── Key computation (spec §2.5) ─────────────────────────────────────────────

def _canonical_fields(row: dict) -> str:
    """Fixed-order normalised string over all canonical fields."""
    rd = row.get("received_date")
    return _SEP.join([
        _to_str(row.get("branch_code", ""), lowercase=True),
        str(rd) if isinstance(rd, date) else "",
        _norm_material_code(row.get("material_code")),
        _to_str(row.get("item_name", "")),              # Thai — not lowercased
        _norm_decimal(row.get("qty")),
        _to_str(row.get("unit", ""), lowercase=True),
        _norm_decimal(row.get("unit_cost")),
        _norm_decimal(row.get("net_cost")),
        _norm_ref(row.get("invoice_no")),
        _norm_ref(row.get("gr_ref")),
        _norm_ref(row.get("po_ref")),
        _to_str(row.get("refill_type", "")),
    ])


def _identity_fields(row: dict) -> str:
    """Same as canonical minus qty/unit_cost/net_cost (the measure fields)."""
    rd = row.get("received_date")
    return _SEP.join([
        _to_str(row.get("branch_code", ""), lowercase=True),
        str(rd) if isinstance(rd, date) else "",
        _norm_material_code(row.get("material_code")),
        _to_str(row.get("item_name", "")),
        _to_str(row.get("unit", ""), lowercase=True),
        _norm_ref(row.get("invoice_no")),
        _norm_ref(row.get("gr_ref")),
        _norm_ref(row.get("po_ref")),
        _to_str(row.get("refill_type", "")),
    ])


def compute_canonical_key(row: dict) -> str:
    """sha256 over fixed-order normalised fields (spec §2.5)."""
    return hashlib.sha256(_canonical_fields(row).encode("utf-8")).hexdigest()


def compute_identity_key(row: dict) -> str:
    """sha256 over identity fields (canonical minus qty/cost) — detects measure edits."""
    return hashlib.sha256(_identity_fields(row).encode("utf-8")).hexdigest()


# ─── Occurrence index (spec §2.5) ────────────────────────────────────────────

def assign_occurrence_indices(rows: list[dict]) -> list[dict]:
    """
    Assign canonical_key, identity_key, and occurrence_index to every row.

    occurrence_index is a 0-based counter within each canonical_key group
    (multiset semantics — order-independent).  Rows sharing the same
    canonical_key are IDENTICAL in every normalised business field, so the
    index assignment is interchangeable.  Reconciliation matches on the
    per-canonical_key COUNT, never on which physical row got index 0 vs 1.
    """
    for row in rows:
        row["canonical_key"] = compute_canonical_key(row)
        row["identity_key"] = compute_identity_key(row)

    counter: dict[str, int] = defaultdict(int)
    for row in rows:
        row["occurrence_index"] = counter[row["canonical_key"]]
        counter[row["canonical_key"]] += 1

    return rows


# ─── Row parser (spec §2.3–2.4) ──────────────────────────────────────────────

def _get(raw: dict, *keys, default=None):
    """Try multiple column-name variants; return first found value."""
    for k in keys:
        if k in raw:
            return raw[k]
    return default


def parse_row(raw: dict, *, row_number: int, branch_code: str) -> dict:
    """
    Parse one raw FoodStory stock-in row dict into the canonical internal form.

    Raises ValueError("Row N: <reason>") if the row has an unrecoverable error
    (e.g. unparseable date).  Blank numeric cells normalise to 0; 'nan'/''
    material_code normalises to None.
    """
    # Date — required
    date_raw = _get(raw, "วันที่", "received_date_raw")
    received_date = _to_date(date_raw)
    if received_date is None:
        raise ValueError(f"Row {row_number}: cannot parse date {date_raw!r}")

    # material_code: 'nan' / '' → None
    mc_raw = _get(raw, "รหัสวัตถุดิบ", "material_code_raw")
    mc = _to_str(mc_raw, lowercase=False)
    material_code = None if mc.lower() in ("nan", "", "none") else mc

    # branch_code: file column takes priority over argument
    branch_raw = _get(raw, "สาขา", "branch_code_raw")
    effective_branch = _to_str(branch_raw) if branch_raw else branch_code

    # Numeric columns — blank → 0
    qty = _to_num(_get(raw, "เติมสินค้า", "qty_raw"))
    unit_cost = _to_num(_get(raw, "ค่าใช้จ่ายต่อหน่วย", "unit_cost_raw"))
    net_cost = _to_num(_get(raw, "ค่าใช้จ่ายสุทธิ", "net_cost_raw"))

    # Optional po_date
    po_date_raw = _get(raw, "วันที่ออก PO", "po_date_raw")
    po_date = _to_date(po_date_raw)  # None is fine

    # Build original_row_json — convert NaN / date objects for JSON
    orig: dict = {}
    for k, v in raw.items():
        if isinstance(v, (date, datetime)):
            orig[str(k)] = str(v)
        elif isinstance(v, float) and pd.isna(v):
            orig[str(k)] = None
        else:
            orig[str(k)] = v

    return {
        "branch_code":       effective_branch,
        "received_date":     received_date,
        "item_name":         _to_str(_get(raw, "ชื่อ", "item_name")),
        "material_code":     material_code,
        "tag":               _to_str(_get(raw, "ป้ายกำกับ", "tag")) or None,
        "refill_type":       _to_str(_get(raw, "ประเภทการเติมวัตถุดิบ", "ประเภท", "refill_type")) or None,
        "invoice_no":        _norm_ref(_get(raw, "INVOICE", "invoice_no")) or "",
        "gr_ref":            _norm_ref(_get(raw, "GR", "gr_ref")) or "",
        "po_ref":            _norm_ref(_get(raw, "PO", "po_ref")) or "",
        "po_date":           po_date,
        "unit":              _to_str(_get(raw, "หน่วย", "unit")) or "",
        "qty":               float(qty),
        "unit_cost":         float(unit_cost),
        "net_cost":          float(net_cost),
        "source_row_number": row_number,
        "original_row_json": orig,
    }


def parse_stock_in_file(df: pd.DataFrame, *, branch_code: str) -> list[dict]:
    """
    Atomically parse the entire DataFrame.  Any single bad row raises ValueError
    (message includes row number) and zero rows are returned — no partial result.

    After parsing, canonical_key / identity_key / occurrence_index are assigned.
    """
    rows: list[dict] = []
    for i, (_, series) in enumerate(df.iterrows(), start=1):
        row_dict = series.to_dict()
        rows.append(parse_row(row_dict, row_number=i, branch_code=branch_code))

    assign_occurrence_indices(rows)
    return rows


# ─── Reconcile diff (pure, no DB) ────────────────────────────────────────────

def reconcile_diff(
    staged: list[dict],
    committed: list[dict],
) -> dict[str, list[dict]]:
    """
    Compute the reconciliation diff between a staged import and the currently
    committed active rows for the same period + branch.

    Each input row must have: canonical_key, identity_key, occurrence_index.

    Returns:
        skip                — staged rows already present in committed (no action)
        insert              — staged rows not in committed (new)
        needs_review        — staged rows whose identity_key matches a committed row
                              but canonical_key differs (qty/cost edited)
        missing_from_reexport — committed active rows absent from the staged set
                              (NOT auto-deleted; user-gated retain/supersede/void)

    Multiset semantics (spec §2.5): matching is by canonical_key COUNT per group,
    not by physical row order.  Two exports with reordered identical rows produce
    the same diff.
    """
    result: dict[str, list[dict]] = {
        "skip": [],
        "insert": [],
        "needs_review": [],
        "missing_from_reexport": [],
    }

    # Index committed by canonical_key (count) and identity_key (set)
    committed_by_ck: dict[str, list[dict]] = defaultdict(list)
    for row in committed:
        committed_by_ck[row["canonical_key"]].append(row)

    committed_ik_set: set[str] = {row["identity_key"] for row in committed}

    # Index staged by canonical_key
    staged_by_ck: dict[str, list[dict]] = defaultdict(list)
    for row in staged:
        staged_by_ck[row["canonical_key"]].append(row)

    staged_ik_set: set[str] = {row["identity_key"] for row in staged}

    # Classify staged rows
    for ck, s_rows in staged_by_ck.items():
        c_rows = committed_by_ck.get(ck, [])
        s_count = len(s_rows)
        c_count = len(c_rows)

        # min(s, c) rows → skip (already committed, same canonical)
        skip_n = min(s_count, c_count)
        result["skip"].extend(s_rows[:skip_n])

        # remaining staged rows → new or changed
        for row in s_rows[skip_n:]:
            if row["identity_key"] in committed_ik_set:
                # Same logical line, different measures → needs human review
                result["needs_review"].append(row)
            else:
                result["insert"].append(row)

    # identity_keys of staged rows classified as needs_review (edited canonical):
    # committed rows whose identity_key matches one of these are being superseded,
    # not truly missing — do NOT flag them as missing_from_reexport.
    # We use needs_review specifically (not skip) so that excess committed rows
    # sharing the same ck/ik as a skip-matched staged row ARE still flagged missing.
    review_ik_set: set[str] = {row["identity_key"] for row in result["needs_review"]}

    # Classify committed rows not fully matched by staged
    for ck, c_rows in committed_by_ck.items():
        s_rows = staged_by_ck.get(ck, [])
        s_count = len(s_rows)
        c_count = len(c_rows)

        if c_count <= s_count:
            continue  # all committed rows for this ck are covered

        # The excess committed rows are absent from the re-export
        missing_n = c_count - s_count
        for row in c_rows[c_count - missing_n:]:
            # Only exempt from missing if a needs_review staged row is taking its place
            if row["identity_key"] not in review_ik_set:
                result["missing_from_reexport"].append(row)

    return result
