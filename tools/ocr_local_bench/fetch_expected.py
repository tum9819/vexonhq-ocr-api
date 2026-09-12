"""Export ground truth (human-confirmed Makro bills + their items + page URLs) to expected.json.
READ-ONLY: connection is set readonly; nothing is written to the DB."""
import json, os, pathlib, decimal, datetime
ROOT = pathlib.Path(__file__).parent
env_path = pathlib.Path(r"C:\Users\rapee\vexonhq-ocr-api\.env")
for line in env_path.read_text(encoding="utf-8-sig").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line and not line.startswith("$"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
import psycopg2
conn = psycopg2.connect(os.environ["DATABASE_URL"])
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor()
cur.execute("""
SELECT vb.id, vb.invoice_no, vb.bill_date, vb.merchant_tax_id, vb.subtotal, vb.vat, vb.amount, vb.payment_type,
       vb.ocr_json->'discount', vb.vendor_name
FROM public.vendor_bills vb
WHERE vb.review_status='confirmed' AND (vb.vendor_name ILIKE '%%ซีพี แอ%%' OR vb.vendor_name ILIKE '%%axtra%%')
ORDER BY vb.bill_date, vb.invoice_no""")
bills = []
for row in cur.fetchall():
    bid, inv, bdate, tax, sub, vat, amt, pt, disc, vname = row
    cur.execute("""SELECT line_no, sku, product_name, quantity, unit, unit_price, amount, source_page
                   FROM public.invoice_items WHERE vendor_bill_id=%s ORDER BY source_page, line_no""", (bid,))
    items = [dict(line_no=r[0], sku=r[1], product_name=r[2], quantity=float(r[3]) if r[3] is not None else None,
                  unit=r[4], unit_price=float(r[5]) if r[5] is not None else None,
                  amount=float(r[6]) if r[6] is not None else None, source_page=r[7]) for r in cur.fetchall()]
    cur.execute("""SELECT page_no, file_name, mime_type, file_url FROM public.attachments
                   WHERE parent_type='vendor_bill' AND parent_id=%s ORDER BY page_no""", (bid,))
    pages = [dict(p=r[0], f=r[1], m=r[2], u=r[3]) for r in cur.fetchall()]
    bills.append(dict(id=str(bid), invoice_no=inv, bill_date=str(bdate), merchant_tax_id=tax,
                      subtotal=float(sub) if sub is not None else None, vat=float(vat) if vat is not None else None,
                      amount=float(amt) if amt is not None else None, payment_type=pt, discount=disc,
                      vendor_name=vname, items=items, pages=pages))
(ROOT / "expected.json").write_text(json.dumps(bills, ensure_ascii=False, indent=1), encoding="utf-8")
print("bills:", len(bills), "items:", sum(len(b["items"]) for b in bills), "attachment rows:", sum(len(b["pages"]) for b in bills))
