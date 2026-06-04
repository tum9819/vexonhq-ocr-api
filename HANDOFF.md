# HANDOFF — FIX: /tax API 500 · vexonhq-ocr-api

**From:** Claude Code → **To:** Antigravity · **Date:** 2026-06-04 · **Requested by:** TUM
**ที่มา:** System audit พบว่า `GET /tax/wht-summary` คืน HTTP 500 เสมอ เพราะ fallback `get_db_conn()` ใน tax_routes.py โยน RuntimeError แทนที่จะ connect DB.

## 🎯 งาน — แก้ 2 บรรทัดใน tax_routes.py

**ไฟล์:** `tax_routes.py` บรรทัด 29-33

### ก่อน (ผิด):
```python
try:
    from main import get_db_conn  # type: ignore
except ImportError:
    def get_db_conn():  # type: ignore
        raise RuntimeError("get_db_conn not available")
```

### หลัง (ถูก):
```python
try:
    from main import get_db_conn  # type: ignore
except ImportError:
    import os
    def get_db_conn():  # type: ignore
        return psycopg2.connect(os.environ["DATABASE_URL"])
```

หมายเหตุ: `psycopg2` ถูก import ไว้แล้ว (บรรทัด 26) — ต้องเพิ่มแค่ `import os` + เปลี่ยน body ของ fallback

## 🛡️ GUARDRAILS
- ✅ แก้เฉพาะ `tax_routes.py` เท่านั้น
- ✅ `pytest tests/ --ignore=tests/test_smoke.py` ต้องผ่าน
- ✅ log ว่าแก้ไฟล์อะไร บรรทัดอะไร
- 🚫 ห้าม commit/push — Claude review → push

<!-- Antigravity: เสร็จ → append '[tax-fix done — file: tax_routes.py / change: ... / pytest: ...]'. ห้าม commit/push. -->
[tax-fix done — file: tax_routes.py / change: updated fallback `get_db_conn` to connect using `psycopg2` and `DATABASE_URL` / pytest: 208 offline tests passed, including `test_tax_summary.py`]
