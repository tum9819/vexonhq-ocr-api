# Runbook — ทยอย push พรุ่งนี้ (2026-05-28)

> เขียนคืน 2026-05-27, **อัปเดตจบ Session 44 (2026-05-28)**. Audit batch 1-12 เสร็จครบ (ทุก backend + ทุก frontend page).
> **Grand total = 44 CRITICAL / 92 MEDIUM / 57 LOW.**
> **ปิดแล้ว 28 CRITICAL + 2 MEDIUM + 1 accepted-risk (B10-C1 RBAC).** เหลือ open 15 CRITICAL — 4 Tax (รอบัญชี), 2 Auth security (B10-C2/C3), 3 design (B7-C4 dedup, B9-C1 slip, B3-C2 ai guardrail), 6 frontend/backend residual.
>
> Session 44 ปิด 20 CRITICAL ใน 9 commits (1 frontend + 8 backend), ไม่มี VPS overload, post-deploy verify ผ่านทุกครั้ง.

## 📜 Session 44 closure log (2026-05-28)

Backend `vexonhq-ocr-api`:
- `766bdc0` — B2-C1, B2-C2, B2-C3, B4-C1, B5-C1, B7-C1, B8-C1/C2 (7 CRITICAL)
- `5b785e9` — B5-C2/C3/C4 void filter sweep 28 queries / 11 endpoints (3 CRITICAL)
- `29870ee` — B7-C2 rollback, B7-C3 async drop, B7-C5 iloc→get (3 CRITICAL)
- `10a788b` — B3-C1 /inventory/current snapshot defense + promo filter (1 CRITICAL)
- `414a858` — B9-C3 statement_rules length guard (1 CRITICAL)
- `6d2b895` — B7-M6 LLM confidence clamp (1 MEDIUM)
- `5be34f3` — B5-M7 net_delta abs() doc comment (1 MEDIUM closed-as-not-bug)

Frontend `VEXONHQ`:
- `295de44` — `safeFetch` + NaN-safe `fmt` refactor across 16 pages, closes B11-C1/C2/C3 + B12-C1/C2 (5 CRITICAL)

Explicit decisions:
- **B10-C1 server-side RBAC** = ACCEPTED RISK (5-user trusted org; page-config UI hide sufficient).

## กฎ deploy (จากบทเรียน VPS วันนี้)
- Push **ทีละ repo ทีละ batch** — เว้นให้ build เสร็จ + CPU < 30% ก่อน push ตัวถัดไป
- **ห้าม** `verify.ps1 -Smoke` ทันทีหลัง deploy — `curl /health` แทน, รอ CPU ลงก่อนค่อย smoke
- VPS ตอนนี้มี swap 4GB แล้ว (เพิ่ม 2026-05-27) → ทนขึ้น แต่ยังควรเว้นระยะ

---

## ✅ SECTION A — แก้แล้วคืนนี้ (verified, พร้อม push เลย)

**7 CRITICAL ใน 5 ไฟล์ backend** — push เป็น **1 commit เดียว** (Python build เบา).
Verified: ast.parse ✅ / verify.ps1 ✅ / SQL A/B ที่ใช้ได้ ✅
ทั้งหมดอ้าง `v_daybook_pnl` ที่ live ใน prod แล้ว (commit 6c4250d) → push ได้ไม่ 500.

| # | ไฟล์ | บั๊กที่แก้ |
|---|---|---|
| **B2-C2** | `recipe_routes.py:735,738` | `log` → `logger` (NameError ทำให้ auto price-sync พังเงียบ) |
| **B2-C1** | `menu_routes.py:3983` | `/pos/food-cost` หาร yield_pct แล้ว (เคย COGS ต่ำกว่าจริง, ขัด /recipes) |
| **B2-C3** | `menu_routes.py:1559,1574,1592` | `/scorecard` ใช้ v_daybook_pnl แทน v_daybook (เคย equity leak) |
| **B4-C1** | `budget_routes.py:281-282` | `/budget/suggest` ลบ `vb.confirmed`+`vb.direction` (column ผี → 500) ใช้ `review_status='confirmed'` |
| **B5-C1** | `menu_routes.py:1347` | `/alerts/summary` ลบ `a.mean_amount` (column ผี → query 500 → anomaly feed เงียบ) |
| **B8-C1/C2** | `line_bot_routes.py:627` | daily LINE digest ใช้ v_daybook_pnl (เคย รายรับ/จ่าย/**กำไรสุทธิ** 06:00 รวม equity) |
| **B7-C1** | `phase3a_ai_categorize_routes.py:597` | `_try_rules()` → `_try_rule_match()` (NameError → cashflow categorize ไม่เคยทำงาน) |

### Paste block (push พรุ่งนี้):
```powershell
cd C:\Users\rapee\vexonhq-ocr-api
git fetch origin
git tag backup-pre-audit-criticals-2026-05-28 origin/main
git push origin backup-pre-audit-criticals-2026-05-28

git add recipe_routes.py menu_routes.py budget_routes.py line_bot_routes.py phase3a_ai_categorize_routes.py
$body = @'
fix(audit): 7 clear CRITICALs from batch 2-8 (equity, hallucinated cols, yield, typos)

B2-C2 recipe_routes.py: log -> logger (NameError silently broke auto price-sync).
B2-C1 menu_routes.py /pos/food-cost: divide recipe cost by yield_pct (match /recipes
  engine) — understated COGS for trimmed ingredients.
B2-C3 menu_routes.py /scorecard: v_daybook_pnl not raw v_daybook (equity leak).
B4-C1 budget_routes.py /budget/suggest: vb.confirmed + vb.direction don't exist
  (500 every call); use review_status='confirmed', drop direction.
B5-C1 menu_routes.py /alerts/summary: drop nonexistent a.mean_amount (500 swallowed
  by bare except -> anomaly feed silently empty).
B8-C1/C2 line_bot_routes.py daily digest: v_daybook_pnl so the 06:00 รายรับ/รายจ่าย/
  กำไรสุทธิ + margin% exclude equity (matched weekly digest; raw leaked Session-6 class).
B7-C1 phase3a_ai_categorize_routes.py: _try_rules() -> _try_rule_match() (NameError ->
  /ai/categorize/cashflow/batch never worked; petty-cash stuck pending).

Verified ast.parse, verify.ps1, live SQL A/B. All reference v_daybook_pnl (live).
From 2026-05-27 audit batches 2-8.
'@
$f = [System.IO.Path]::GetTempFileName()
[System.IO.File]::WriteAllText($f, $body)
git commit -F $f
Remove-Item $f
git push origin main
Start-Sleep -Seconds 90
curl.exe https://api.marastation.com/health
```
หลัง health 200 + รอ CPU ลง → `.\verify.ps1 -Smoke` ยืนยัน 64/64

> **หมายเหตุ**: B2-C1 (yield) + B2-C3/B8 (equity) ตัวเลขเดือนปัจจุบันอาจไม่เปลี่ยน (ไม่มี recipe yield<100, พ.ค. ไม่มี equity) — แต่ fix ถูกต้อง + เดือนที่มี equity (พ.ย.-เม.ย.) ถูกต้องทันที.

---

## 🟡 SECTION B — DEFERRED CRITICALs (ต้อง TUM ตัดสิน / เสี่ยงเกินทำ unattended)

### B5-C2/C3/C4 — Void leak (ขาด `AND b.bill_net > 0`) — **11 endpoints**
**ทำไม defer**: เป็น 1-liner ต่อ query แต่กระจาย ~11 จุดใน menu_routes.py — แก้ blind unattended เสี่ยง bug แทรก. ควรทำเป็น session โฟกัส (ผมทำให้ + verify ทีละตัวได้เช้านี้ถ้า TUM ว่าง).
**Endpoints ที่ leak** (เพิ่ม `AND b.bill_net > 0` ใน WHERE ที่ JOIN pos_bills): `/menu/performance`, `/menu/trends`, `/pos/items`, `/pos/menu-engineering`, `/pos/compare` (panel), `/pos/calendar`, `/pos/flash` (panel), `/pos/categories`, `/pos/combos`, discount summary ใน `/pos/payments` + `/pos/discounts`.
**Impact**: void bills (net=0, line items ไม่ถูกลบ) พอง revenue/qty บนหน้าที่ใช้ตัดสิน keep/cut เมนู. `/pos/compare` + `/pos/flash` headline กรองแล้วแต่ panel ไม่กรอง → หน้าจอขัดกันเอง.
รายละเอียด: `2026-05-27-batch5-pos-analytics-audit.md`

### B3-C1 — `/inventory/current` (phase2_routes.py:714) ไม่ใช้ snapshot defense + ไม่กรอง promo
**ทำไม defer**: ต้อง mirror pattern `_get_latest_snapshot_id` + promo filter ให้ตรงกับ `/stock/*` — ต้องอ่าน logic ข้าง ๆ ให้ชัวร์ก่อน (ไม่อยากเดา).
**Impact**: ตัวเลข stock หน้าหลักอาจมาจาก upload แถวเดียว + รวม promo pack, ขัดกับ /stock/* และ /inventory/reorder.

### B3-C2 — ai-order-advice ไม่มี min-sample guard
**ทำไม defer**: เป็น **product decision** — min sample เท่าไร? แสดงอะไรแทนถ้าข้อมูลน้อย? TUM ตัดสิน.

### B4-C2 — `app/budget/page.tsx` ไม่เช็ก res.ok (frontend)
**ทำไม defer**: frontend fix (ต้อง build+deploy แยก). ง่าย แต่จัดเป็น frontend batch. ผมเตรียมให้ได้เช้านี้.

### B4-C3 — `app/budgets/page.tsx` field shape ไม่ตรง backend
**ทำไม defer**: ต้อง verify ว่ามี router `/budgets` แยก (ต่างจาก `/budget/*`) จริงมั้ย — ถ้า contract ไม่ตรงจริงเป็นบั๊กใหญ่ ต้องดูให้ละเอียด.

### B6-C1, B6-C2 — WHT base net-vs-gross + category ขาด (tax_routes.py)
**ทำไม defer**: 🛑 **domain/บัญชีตัดสิน** — ต้องรู้ว่าจ่าย supplier แบบ net-of-tax หรือ gross. ผิดทาง = ยื่นภาษีผิด. **อย่าแก้เองเด็ดขาด** — ถาม TUM/บัญชีก่อน.

### B6-C3 — musician-fee `int()` truncation (phase12:292)
**ทำไม defer**: phase12 เป็น coordination zone (bank statement parsing) + กระทบ classification. แก้ได้ตรง ๆ (ใช้ exact amount) แต่ขอ TUM confirm ก่อนแตะ parser.

### B6-C4 — dedup key ตัด time+balance (phase12:393)
**ทำไม defer**: 🛑 เปลี่ยน dedup key กระทบ **data เดิม** (อาจต้อง re-dedup) — ต้องวางแผน migration. เสี่ยงสุดในชุดนี้.

---

## 🟡 SECTION B2 — DEFERRED CRITICALs จาก Round 2 (batch 7-12)

### 🔒 Auth/security (batch 10) — 3 ตัว — ต้อง TUM ตัดสิน + วางแผน (อย่าแตะ unattended)
- **B10-C1** ไม่มี server-side RBAC — staff token เข้าถึงทุก endpoint การเงิน. ✋ **TUM decision 2026-05-28: ACCEPTED RISK.** 5-user organization, may/toon/oil = trusted employees, page-config UI hide เพียงพอสำหรับ context นี้. ไม่ implement backend RBAC. ทบทวนใหม่ถ้าเพิ่ม user หรือมี untrusted staff.
- **B10-C2** secret ใน URL query (snapshots/alerts) → leak ใน log. **แก้ = ย้ายไป header** → ต้องแก้ Uptime Robot/cron caller ด้วย.
- **B10-C3** verify_token fallthrough + hardcoded ES256 aud — Session-41 breakage class. **อย่าแตะ auth flow โดยไม่ทดสอบเต็ม.**

### 🖥️ Frontend res.ok/NaN (batch 11+12) — 7 ตัว — ทำเป็น **refactor session เดียว** (shared helper)
- **B11-C1**: 14/22 POS หน้าไม่เช็ก res.ok → 500 = แสดง ฿0/ว่าง เป็นความจริง (Session-18 class)
- **B11-C2**: 6 หน้า hardcode domain เก่า `api.vexonhq.com`
- **B11-C3**: NaN money บน goals/predict
- **B12-C1/C2**: delivery + revenue ไม่เช็ก res.ok + divide-by-zero NaN%
- **วิธีที่แนะนำ**: สร้าง `lib/safeFetch.ts` (throw on !res.ok) + null-safe `fmt()` กลาง → แทนที่ทั้ง ~16 หน้า. เป็น refactor ใหญ่ frontend → 1 commit, build + deploy, review ก่อน. **ไม่ blind-edit 16 ไฟล์ overnight.**
- หมายเหตุ: ทุก money-WRITE path (POST/PATCH/DELETE) ใน batch 12 **ปลอดภัยแล้ว** (มี res.ok ครบ) — ปัญหาอยู่ฝั่ง read display เท่านั้น.

### 📥 Ingestion (batch 7) — 4 ตัว — pos_import อ่อนไหว, ระวัง
- **B7-C2** batch loop ไม่ rollback → 1 fail พิษทั้ง batch (แก้ง่าย additive แต่ touch transaction — ทำพร้อม review)
- **B7-C3** `pos_import.py:1332` async def block event loop (Session-36 class) → `asyncio.to_thread` (import path สำคัญ)
- **B7-C4** pos_sales_items INSERT ไม่ dedup → re-import นับซ้ำ (**design decision — data integrity**)
- **B7-C5** `pos_import.py:493` `r.iloc[4]` แทน `r.get("ส่วนลด")` → เงินผิด (verify column ก่อน)

### 💰 Slip/tax/rules (batch 9) — 3 ตัว
- **B9-C1** slip-match tolerance ±10฿ → match บิลผิด (เคลียร์ AP ผิด). แก้ = epsilon-tier + ambiguous flag (design)
- **B9-C2** PND3 WHT whitelist + flat 3% gross → 🛑 **ภาษี domain (บัญชี)** — ยื่น สรรพากร ผิดได้
- **B9-C3** rules `match_value` สั้น/ว่าง → ILIKE '%x%' catch-all (แก้ = length guard, แต่ touch classification)

### LINE bot residual (batch 8) — handled
- B8-C1/C2 **แก้แล้ว** (Section A). M1/M2 (quick-expense parser นับเลขผิด/ไม่ classify intent) เป็น MEDIUM — flag ใน batch8 report.

---

## 🔵 SECTION C — MEDIUM (62) + LOW (39)

ไม่มีตัวไหน money-at-risk หลังปิด CRITICAL. อยู่ในรายงานแต่ละ batch (12 ไฟล์):
- batch2 foodcost-recipes, batch3 stock-inventory, batch4 arap-cashflow-budget,
  batch5 pos-analytics, batch6 tax-bankstatement, batch7 ingestion-ai,
  batch8 linebot-quickentry, batch9 payments-slips-supplier, batch10 auth-infra,
  batch11 pos-frontend, batch12 other-frontend (+ batch1 = P&L+Daybook ปิดครบแล้ว)

แนะนำ: คัดเฉพาะ MED ที่ผ่าน lean-system bar — ที่เหลือข้ามได้.

---

## ลำดับแนะนำพรุ่งนี้
1. **Section A** — push 1 commit (7 CRITICAL ที่ verify แล้ว) — เช้า, ง่ายสุด, deploy เดียว
2. **B9-C2 + B6-C1/C2** — คุยบัญชีเรื่อง WHT/PND3 net-gross (ไม่ใช่งานโค้ด — ตัดสินก่อน)
3. **Frontend safeFetch refactor** (B11/B12 — 16 หน้า) — ผมทำ+build+verify ให้ 1 session โฟกัส
4. **B10 auth RBAC** — ตัดสิน endpoint admin-only แล้วผมเพิ่ม role-gate (security, ค่อย ๆ)
5. **B5 void filters** (11 endpoints) + **pos_import** (B7-C3/C5) + **inventory B3-C1** — backend batch ทำ+verify ทีละตัว
6. ที่เหลือ (dedup B7-C4, slip tolerance B9-C1, ai guardrail B3-C2) — design decision, คุยกันก่อน

> ทุก fix คืนนี้ **ยังไม่ push** — อยู่ใน working tree ของ vexonhq-ocr-api (5 ไฟล์โค้ด + 7 audit reports + runbook นี้). พรุ่งนี้ push Section A commit เดียวพอ.
