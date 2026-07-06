"""Verify the KBank statement parser against the statement's own checksum.

Every KBank PDF prints a summary line:  รวมฝากเงิน N รายการ <sum>  /  รวมถอนเงิน N รายการ <sum>
That is a built-in checksum. This script runs the line-based parser and asserts the
parsed deposit/withdrawal count+sum EXACTLY equals that summary, per file.

Usage:  python scripts/verify_statement_parse.py <file1.pdf> [file2.pdf ...]
Exit 0 = all match (zero drift). Exit 1 = drift found.

The parse_kbank() here is the reference implementation that phase12_bank_statement_routes.py
_extract_transactions mirrors. Keep them in sync.
"""
import io
import re
import sys
from datetime import date

DATE_TIME = re.compile(r"^(\d{2})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})\s+(.*)$")
MONEY = re.compile(r"\d[\d,]*\.\d{2}")
# dated lines that are NOT transactions
SKIP_IN_REST = ("ยอดยกมา", "ยอดยกไป")
STATEMENT_BOILERPLATE_MARKERS = (
    "ออกโดย K PLUS",
    "PAGE/OF",
    "ชื่อบัญชี",
    "เลขที่บัญชีเงินฝาก",
    "รอบระหว่างวันที่",
    "สาขาเจ้าของบัญชี",
    "เวลา/ ยอดคงเหลือ",
    "วันที่ รายการ ถอนเงิน / ฝากเงิน",
)


def is_statement_boilerplate_continuation(line: str) -> bool:
    text = (line or "").strip()
    if not text:
        return False
    return any(marker in text for marker in STATEMENT_BOILERPLATE_MARKERS)


def clean_statement_detail(detail: str) -> str:
    text = re.sub(r"\s+", " ", (detail or "").strip())
    for marker in (")52-30(", ")1.", "V-AS_AC", "FDPBK", " DD.048", " วันที่มีผล"):
        pos = text.find(marker)
        if pos >= 0:
            text = text[:pos].strip()
    text = re.sub(r"\s+ที่\s+\d+/\d+\(\d+\)\s*$", "", text).strip()
    return text


def parse_kbank(pdf_bytes: bytes) -> list[dict]:
    """Line-based KBank parser. Direction is taken from the running-balance delta
    (the balance column is ground truth); the transaction-type word is a fallback
    only for the very first row. Wrapped (no-date) lines append to the prior detail.
    """
    import pdfplumber

    rows: list[dict] = []
    prev_balance: float | None = None

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for raw in text.split("\n"):
                line = raw.strip()
                if not line:
                    continue
                m = DATE_TIME.match(line)
                if not m:
                    # wrapped continuation of the previous transaction's detail
                    if (
                        rows
                        and "ยอดยก" not in line
                        and "รวม" not in line[:4]
                        and not is_statement_boilerplate_continuation(line)
                    ):
                        rows[-1]["description"] = clean_statement_detail(
                            rows[-1]["description"] + " " + line
                        )
                    continue

                dd, mo, yy = int(m.group(1)), int(m.group(2)), int(m.group(3)) + 2000
                rest = m.group(6).strip()

                # opening/closing balance carry line: "ยอดยกมา 149.53" — reset baseline, no row
                if any(t in rest for t in SKIP_IN_REST):
                    bal = MONEY.findall(rest)
                    if bal:
                        prev_balance = float(bal[-1].replace(",", ""))
                    continue

                monies = list(MONEY.finditer(rest))
                if len(monies) < 2:
                    continue  # a real txn line always has amount + running balance

                amount = float(monies[0].group().replace(",", ""))
                balance = float(monies[1].group().replace(",", ""))
                detail = clean_statement_detail(rest[monies[1].end():])
                type_word = rest.split()[0]

                if prev_balance is not None:
                    delta = round(balance - prev_balance, 2)
                    is_income = delta > 0
                else:
                    is_income = type_word.startswith("รับ") or "ดอกเบี้ย" in type_word

                prev_balance = balance
                try:
                    txn = date(yy, mo, dd)
                except ValueError:
                    continue

                rows.append({
                    "txn_date": txn,
                    "description": detail,
                    "debit": 0.0 if is_income else amount,
                    "credit": amount if is_income else 0.0,
                    "balance": balance,
                    "type_word": type_word,
                })
    return rows


def pdf_checksum(pdf_bytes: bytes) -> dict:
    """Read the statement's own รวมฝาก/รวมถอน summary line."""
    import pdfplumber

    out = {"dep_n": None, "dep_sum": None, "wd_n": None, "wd_sum": None}
    num = re.compile(r"(\d+)\s+รายการ\s+([\d,]+\.\d{2})")
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for ln in (page.extract_text() or "").split("\n"):
                if "รวมฝาก" in ln:
                    g = num.search(ln)
                    if g:
                        out["dep_n"], out["dep_sum"] = int(g.group(1)), float(g.group(2).replace(",", ""))
                if "รวมถอน" in ln:
                    g = num.search(ln)
                    if g:
                        out["wd_n"], out["wd_sum"] = int(g.group(1)), float(g.group(2).replace(",", ""))
    return out


def verify(path: str) -> bool:
    with open(path, "rb") as f:
        data = f.read()
    rows = parse_kbank(data)
    chk = pdf_checksum(data)

    dep_n = sum(1 for r in rows if r["credit"] > 0)
    dep_sum = round(sum(r["credit"] for r in rows), 2)
    wd_n = sum(1 for r in rows if r["debit"] > 0)
    wd_sum = round(sum(r["debit"] for r in rows), 2)

    ok = (dep_n == chk["dep_n"] and abs(dep_sum - (chk["dep_sum"] or 0)) < 0.01
          and wd_n == chk["wd_n"] and abs(wd_sum - (chk["wd_sum"] or 0)) < 0.01)

    print(f"\n=== {path.split('/')[-1]} ===")
    print(f"  deposits : parsed {dep_n}/{dep_sum:,.2f}   statement {chk['dep_n']}/{(chk['dep_sum'] or 0):,.2f}   drift {dep_n-(chk['dep_n'] or 0):+d} / {dep_sum-(chk['dep_sum'] or 0):+,.2f}")
    print(f"  withdraws: parsed {wd_n}/{wd_sum:,.2f}   statement {chk['wd_n']}/{(chk['wd_sum'] or 0):,.2f}   drift {wd_n-(chk['wd_n'] or 0):+d} / {wd_sum-(chk['wd_sum'] or 0):+,.2f}")
    print(f"  => {'PASS (zero drift)' if ok else 'FAIL (drift vs statement)'}")
    return ok


if __name__ == "__main__":
    paths = sys.argv[1:]
    results = [verify(p) for p in paths]
    sys.exit(0 if all(results) else 1)
