# HANDOFF — SEC-1b: Remove /ai/exec from PUBLIC_PATHS · vexonhq-ocr-api

**From:** Claude Code → **To:** Antigravity · **Date:** 2026-06-04 · **Requested by:** TUM
**ที่มา:** `/ai/exec` ยังอยู่ใน `PUBLIC_PATHS` → bypass JWTAuthMiddleware ได้โดยไม่ต้องมี Bearer token. `compare_digest` + whitelist + no-shell ถูก fix แล้วใน A− round แต่การที่ endpoint public ยังเป็น risk ที่ต้องปิด.

---

## ⚠️ Deploy Order (สำคัญมาก)

ต้อง deploy **marastation-ai ก่อน** (HANDOFF.md ในนั้น) แล้วค่อย push repo นี้  
ถ้า push repo นี้ก่อน AI chat จะ 401 ตลอด

---

## 🎯 งาน — แก้ 1 บรรทัดใน main.py

**ไฟล์:** `main.py` บรรทัด 330

ก่อน:
```python
PUBLIC_PATHS = {"/", "/health", "/health/deep", "/cron/health", "/auth/login", "/auth/logout", "/docs", "/openapi.json", "/redoc", "/alerts/uptime-webhook", "/alerts/test-telegram", "/alerts/discord-interaction", "/alerts/discord-restart-test", "/line/webhook", "/snapshots/status", "/snapshots/auto-rotate", "/menu/public", "/ai/exec"}
```

หลัง (ลบแค่ `"/ai/exec"` ออก):
```python
PUBLIC_PATHS = {"/", "/health", "/health/deep", "/cron/health", "/auth/login", "/auth/logout", "/docs", "/openapi.json", "/redoc", "/alerts/uptime-webhook", "/alerts/test-telegram", "/alerts/discord-interaction", "/alerts/discord-restart-test", "/line/webhook", "/snapshots/status", "/snapshots/auto-rotate", "/menu/public"}
```

## 🛡️ GUARDRAILS
- ✅ แก้เฉพาะ `main.py` บรรทัด 330 เท่านั้น
- ✅ `pytest tests/ -q --ignore=tests/test_smoke.py --ignore=tests/test_backup_prune.py` ต้องผ่าน
- 🚫 ห้าม push — รอ marastation-ai deploy ก่อน → Claude push + verify

<!-- Antigravity: เสร็จ → append '[sec1b done — file: main.py / change: removed /ai/exec from PUBLIC_PATHS / pytest: ...]'. ห้าม commit/push. -->
