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


def normalize_branch_code(branch: str) -> str:
    """
    Normalize branch code / branch name to canonical lowercase code.
    If name is in Thai or has aliases, map them to the canonical code.
    Supported maps:
        'ทวีวัฒนา' / 'thawi_watthana' -> 'thawi_watthana'
    """
    s = str(branch).strip().lower()
    mapping = {
        "ทวีวัฒนา": "thawi_watthana",
        "thawi_watthana": "thawi_watthana",
        "thawi-watthana": "thawi_watthana",
    }
    if s in mapping:
        return mapping[s]
    raw_s = str(branch).strip()
    if raw_s in mapping:
        return mapping[raw_s]
    return s


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

    # branch_code: file column takes priority over argument and must match normalized request branch
    branch_raw = _get(raw, "สาขา", "branch_code_raw")
    canonical_request_branch = normalize_branch_code(branch_code)

    if branch_raw:
        file_branch_str = str(branch_raw).strip()
        canonical_file_branch = normalize_branch_code(file_branch_str)
        if canonical_file_branch != canonical_request_branch:
            raise ValueError(
                f"Row {row_number}: branch {file_branch_str!r} does not match import branch {branch_code!r}"
            )
        effective_branch = canonical_request_branch
    else:
        effective_branch = canonical_request_branch

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
        date_raw = _get(row_dict, "วันที่", "received_date_raw")
        if date_raw:
            date_str = str(date_raw).strip()
            if date_str.lower() in ("total", "รวม") or "รวม" in date_str:
                continue
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
        skip                  — staged rows already present in committed (no action)
        insert                — staged rows not in committed (new)
        needs_review          — staged rows whose identity_key matches a committed row
                                but canonical_key differs (qty/cost edited)
        missing_from_reexport — committed active rows absent from the staged set
                                (NOT auto-deleted; user-gated retain/supersede/void)

    Multiset semantics (spec §2.5): matching is by canonical_key COUNT per group,
    not by physical row order.  Two exports with reordered identical rows produce
    the same diff.
    """
    def _sort_key(row: dict) -> tuple:
        return (
            str(row.get("canonical_key") or ""),
            int(row.get("occurrence_index") or 0),
            int(row.get("source_row_number") or 0),
            str(row.get("id") or "")
        )

    def _c_id(row: dict) -> str:
        return str(row.get("id") or id(row))

    def _s_id(row: dict) -> str:
        return str(row.get("source_row_number") or id(row))

    staged_sorted = sorted(staged, key=_sort_key)
    committed_sorted = sorted(committed, key=_sort_key)

    # Initialize unmatched pools
    unpaired_staged = list(staged_sorted)
    unpaired_committed = list(committed_sorted)

    skip_rows = []
    needs_review_rows = []
    insert_rows = []

    # 1. Match exact (canonical_key, occurrence_index)
    matched_staged_indices = set()
    matched_committed_ids = set()

    for s_row in unpaired_staged:
        s_ck = s_row["canonical_key"]
        s_oi = s_row["occurrence_index"]
        for c_row in unpaired_committed:
            c_id = _c_id(c_row)
            if c_id in matched_committed_ids:
                continue
            if c_row["canonical_key"] == s_ck and c_row["occurrence_index"] == s_oi:
                skip_rows.append(s_row)
                matched_staged_indices.add(_s_id(s_row))
                matched_committed_ids.add(c_id)
                break

    unpaired_staged = [r for r in unpaired_staged if _s_id(r) not in matched_staged_indices]
    unpaired_committed = [r for r in unpaired_committed if _c_id(r) not in matched_committed_ids]

    # 2. Match exact canonical_key only
    matched_staged_indices = set()
    matched_committed_ids = set()

    for s_row in unpaired_staged:
        s_ck = s_row["canonical_key"]
        for c_row in unpaired_committed:
            c_id = _c_id(c_row)
            if c_id in matched_committed_ids:
                continue
            if c_row["canonical_key"] == s_ck:
                skip_rows.append(s_row)
                matched_staged_indices.add(_s_id(s_row))
                matched_committed_ids.add(c_id)
                break

    unpaired_staged = [r for r in unpaired_staged if _s_id(r) not in matched_staged_indices]
    unpaired_committed = [r for r in unpaired_committed if _c_id(r) not in matched_committed_ids]

    # 3. Match remaining by identity_key one-to-one
    staged_by_ik = defaultdict(list)
    for r in unpaired_staged:
        staged_by_ik[r["identity_key"]].append(r)

    committed_by_ik = defaultdict(list)
    for r in unpaired_committed:
        committed_by_ik[r["identity_key"]].append(r)

    matched_staged_indices = set()
    matched_committed_ids = set()

    all_iks = set(staged_by_ik.keys()).union(committed_by_ik.keys())
    for ik in sorted(all_iks):
        s_list = staged_by_ik[ik]
        c_list = committed_by_ik[ik]
        pair_count = min(len(s_list), len(c_list))
        for i in range(pair_count):
            s_row = s_list[i]
            c_row = c_list[i]
            # Copy staged row and assign counterpart_id without mutating original dict
            s_copy = dict(s_row)
            s_copy["counterpart_id"] = c_row.get("id") or str(id(c_row))
            needs_review_rows.append(s_copy)
            matched_staged_indices.add(_s_id(s_row))
            matched_committed_ids.add(_c_id(c_row))

    unpaired_staged = [r for r in unpaired_staged if _s_id(r) not in matched_staged_indices]
    unpaired_committed = [r for r in unpaired_committed if _c_id(r) not in matched_committed_ids]

    # 4. Leftovers
    insert_rows.extend(unpaired_staged)
    missing_rows = list(unpaired_committed)

    return {
        "skip": skip_rows,
        "insert": insert_rows,
        "needs_review": needs_review_rows,
        "missing_from_reexport": missing_rows,
    }
