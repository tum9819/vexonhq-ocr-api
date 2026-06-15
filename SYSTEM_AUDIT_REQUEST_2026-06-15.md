# System Audit Request — Full VEXONHQ Stack
**Date:** 2026-06-15  
**Requester:** TUM  
**Requested By:** Claude Code  
**Auditor:** Google Antigravity (Gemini)

---

## Context

Just deployed PR #10 (commit `4dc5ca5`) with 4 system accuracy fixes:
1. **F-STK-1:** Inventory NaN sanitization (all 8 fields in pos_import.py)
2. **F-OCR-3:** OCR structured outputs with env toggle (`OCR_STRUCTURED=0`)
3. **F-DASH-2:** Dashboard v_invoice_due_soon view timezone fix
4. **Test Cleanup:** sys.modules pollution in test_ai_exec.py

Backup tag: `backup/accuracy-audit-2026-06-15`  
Live URL: `https://api.marastation.com` (DigitalOcean + Coolify auto-deploy)

---

## Audit Scope — Full Stack

### 1️⃣ Web Frontend (`marastation-web`)
**Repo:** `C:\Users\rapee\marastation-web`  
**Focus Areas:**
- [ ] Dashboard dashboard / invoice due-soon display (should show 0 bills after fix)
- [ ] Inventory page — verify material codes are strings, not `'nan'`
- [ ] OCR upload flow — test structured output behavior + fallback (`OCR_STRUCTURED=0`)
- [ ] Overall UI/UX consistency, performance, edge cases
- [ ] Type safety (TypeScript), component reuse, prop drilling
- [ ] Error handling & user feedback for failures

### 2️⃣ OCR API Backend (`vexonhq-ocr-api` — just deployed)
**Repo:** `C:\Users\rapee\vexonhq-ocr-api`  
**Live:** `https://api.marastation.com`  
**Focus Areas:**
- [ ] POST `/ocr/upload` — test with real PDF/image; verify structured output path
- [ ] `/health/deep` — Postgres, Supabase, config presence
- [ ] `/cron/health` — job staleness detection working
- [ ] POS import endpoints (`/pos/import`, `/pos/detect-only`)
- [ ] Error handling, logging clarity, slow query warnings
- [ ] API response shapes, field validation

### 3️⃣ Main VEXONHQ Backend (`VEXONHQ`)
**Repo:** `C:\Users\rapee\VEXONHQ`  
**Focus Areas:**
- [ ] Dashboard executive cards accuracy (P&L, revenue, expenses, cashflow)
- [ ] Due-soon bill logic consistency with updated view
- [ ] LINE bot daily digest, health checks, alert routing
- [ ] Payment status tracking, vendor bill matching
- [ ] Receipt/invoice processing pipeline

### 4️⃣ Data Integrity & Accuracy
**Focus Areas:**
- [ ] Inventory: material_code, tag fields no longer have `'nan'` strings
- [ ] AP bills: 21 old unpaid bills correctly marked as `payment_status='paid'`
- [ ] Invoices: OCR field presence/types with structured outputs
- [ ] Dashboard: "Due Soon" count now matches reality (was 32, should be ~0-3)

### 5️⃣ Infrastructure & Operations
**Stack:** Supabase (Postgres + RLS) + DigitalOcean (Coolify) + OpenAI + Anthropic APIs  
**Focus Areas:**
- [ ] Database migrations: `fix_v_invoice_due_soon_use_payment_status` applied correctly
- [ ] Environment variables: all required secrets set, no leaks
- [ ] Coolify auto-build/deploy working smoothly
- [ ] Monitoring: Uptime Robot, health endpoints, logging clarity
- [ ] Backup/rollback: `backup/accuracy-audit-2026-06-15` tag safe to use

---

## Known Issues (Non-P0 Backlog)

**Optional cleanups not yet addressed:**
1. **31 bills with NULL `due_date`** — need manual review + update
2. **Vendor name normalization** — multiple spellings for same vendor

---

## Rollback Plan

If Antigravity finds critical issues:

**OCR fallback (no redeploy needed):**
```bash
# Set in Coolify env
OCR_STRUCTURED=0
```

**Full rollback:**
```bash
git checkout backup/accuracy-audit-2026-06-15
# Coolify will auto-rebuild
```

---

## Questions for Antigravity

1. Are the 4 fixes working as intended in production?
2. Any data inconsistencies or edge cases missed?
3. Are there architectural risks or tech debt exposed by this audit?
4. Priority ordering for the 2 optional backlog items?
5. Anything else the system should address before next push?

---

## Sign-Off

- **Deploy Date:** 2026-06-15
- **Backup Tag:** `backup/accuracy-audit-2026-06-15` ✅ Created
- **Test Suite:** 382 passed, 1 skipped ✅ Verified
- **Ready for Audit:** ✅ Yes

