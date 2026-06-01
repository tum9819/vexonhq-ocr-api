# Selling Price Calculator — Design Spec

> Date: 2026-06-01
> Repo: vexonhq-ocr-api (backend) + VEXONHQ (frontend `/recipes`)
> Status: design approved by TUM, ready for implementation plan
> Closes: gap #15 from RestoSheet-vs-VEXONHQ feature comparison
> (VEXONHQ มีต้นทุน/yield/GP descriptive อยู่แล้ว แต่ยังไม่มี "ตั้ง target → เสนอราคา" แบบ forward, และไม่แยกราคาตามช่องทาง)

---

## 1. Problem / Gap

VEXONHQ คำนวณ **GP ของราคาที่ตั้งไปแล้ว** (`recipe_routes._calc_cost` → `gp_pct`) — descriptive อย่างเดียว
ที่ขาดเทียบ RestoSheet Pro:

1. **Forward calc** — กรอก target cost% (หรือ GP%) แล้วให้ระบบ "เสนอราคาที่ควรขาย" กลับมา
2. **แยกราคาตามช่องทาง** — `recipes` ตอนนี้มี `selling_price` ช่องเดียว; delivery ที่โดน platform หักคอม (~32%) ทำให้ GP จริงต่ำกว่าหน้าร้านมาก แต่ระบบมองไม่เห็น

## 2. Goals

- ต่อ 1 เมนู เสนอราคาขายที่ "ควรขาย" ใน 3 ช่องทาง: หน้าร้าน / กลับบ้าน / Delivery
- คิดทั้ง **forward** (target% → ราคา) และ **reverse** (ราคา → cost% + GP สุทธิจริง)
- โชว์ **GP สุทธิหลังหักค่าคอม platform** เป็นผลลัพธ์ (จุดที่ delivery กำไรหด)
- ปัดเศษราคาแบบ charm pricing เลือกได้ (฿9 / ฿0 / ฿5 / ไม่ปัด)
- เขียนราคาที่เลือกกลับ DB ต่อช่องทางได้
- Reuse ต้นทุนจริง (`_calc_cost`, รวม yield) — ไม่ให้ TUM กรอกต้นทุนซ้ำ (นี่คือ moat ที่ Sheets ไม่มี)

## 3. Non-Goals (YAGNI — กันบวม)

- ไม่แยกค่าคอมรายแพลตฟอร์ม (Grab vs LINEMAN แยกกัน) — ใช้ delivery ช่องเดียว commission เดียว
- ไม่ทำ per-recipe packaging override (ค่ากล่องต่อเมนู) — ใช้ค่ากล่อง default ต่อช่องทางก่อน
- ไม่ทำ price history / A-B test ราคา / auto-repricing
- ไม่แตะ POS import flow หรือดัน suggested price กลับ `/pos/prices`
- ไม่ refactor ค่าคอม hardcode ใน `pos_import.py` (`_LINEMAN_GP_RATE = 0.321`) ในงานนี้ — แค่ย้าย default มาไว้ใน config (ดู §9)

---

## 4. Core Formula (หัวใจ)

ต่อ 1 เมนู ต่อ 1 ช่องทาง:

```
channel_cost = food_cost + packaging_cost
```
- `food_cost` = `_calc_cost(recipe_id)["total_cost"]` (รวม yield อยู่แล้ว)
- `packaging_cost` = จาก config ต่อช่องทาง (หน้าร้าน = 0)
- `commission_pct` = จาก config ต่อช่องทาง (หน้าร้าน/กลับบ้าน = 0, delivery default 32.1)

### Forward — target% → ราคา

```
mode = "cost":  price_raw = channel_cost / (target_pct / 100)
mode = "gp"  :  price_raw = channel_cost / (1 - target_pct / 100)
```
(สองโหมดเทียบเท่ากันเมื่อ commission = 0: cost% = 100 − gp%. target คิดบน "ราคาเมนู" ไม่รวมคอม)

แล้วปัดเศษ → `suggested_price` (ดู §5) จากนั้นคิด GP สุทธิจาก **ราคาที่ปัดแล้ว**:

```
net_gp_baht = suggested_price * (1 - commission_pct / 100) - channel_cost
net_gp_pct  = net_gp_baht / suggested_price * 100
low_margin  = net_gp_pct < LOW_MARGIN_PCT   (default 40)
```

### Reverse — ราคา → cost% + GP สุทธิ

ใช้สูตรเดียวกันกลับทาง (รับ `price` ที่ตั้งอยู่):
```
cost_pct   = channel_cost / price * 100
net_gp_pct = (price * (1 - commission_pct/100) - channel_cost) / price * 100
```

### Worked example (food_cost = 42.00 ฿, target = cost 30%, rounding = ฿9)
> สมมติ TUM ตั้งค่ากล่อง (packaging) = 5 ฿ สำหรับกลับบ้าน/delivery ใน config (seed default = 0)

| ช่องทาง | packaging | commission | channel_cost | price_raw | suggested (฿9) | net GP% |
|---|---|---|---|---|---|---|
| หน้าร้าน | 0 | 0% | 42.00 | 140.00 | **149** | 71.8% |
| กลับบ้าน | 5 | 0% | 47.00 | 156.67 | **159** | 70.4% |
| Delivery | 5 | 32% | 47.00 | 156.67 | **159** | **38.4% ⚠** |

→ เมนูเดียว ราคาเดียว (159) แต่ delivery เหลือ GP สุทธิ 38% เพราะคอม 32% — เลขนี้คือสิ่งที่ต้องเห็นก่อนตั้งราคา

---

## 5. Rounding modes

ทุกโหมด **ปัดขึ้น** เสมอ เพื่อไม่ให้ margin ต่ำกว่าเป้า:

| mode | นิยาม | ตัวอย่าง 156.67 |
|---|---|---|
| `"9"` | จำนวนเต็มน้อยสุดที่ ≥ price_raw และลงท้าย 9 | 159 |
| `"0"` | ปัดขึ้นใกล้สุดที่หาร 10 ลงตัว = `ceil(p/10)*10` | 160 |
| `"5"` | ปัดขึ้นใกล้สุดที่หาร 5 ลงตัว = `ceil(p/5)*5` | 160 |
| `"none"` | ปัดขึ้นเป็นจำนวนเต็มบาท = `ceil(p)` | 157 |

(ถ้า price_raw ลงท้าย 9 อยู่แล้ว เช่น 149.0 → คง 149)

---

## 6. Data Model

ทางเลือกที่รับ: **เพิ่ม 2 คอลัมน์ + ตาราง config** (เบา, ไม่พังโค้ดที่อ่าน `selling_price`, ปลด hardcode คอมออกมาเป็นค่าที่แก้ได้)

### Migration `migrations/<date>_selling_price_channels.sql`
> ชื่อไฟล์ตั้งวันที่ตอน implement ตาม convention เดิมใน `migrations/`

> ก่อนรัน: verify ชื่อคอลัมน์เดิมกับ `information_schema.columns` (กฎ CLAUDE.md #6)

```sql
-- 1. ราคาต่อช่องทาง (selling_price คงไว้ = หน้าร้าน/dine_in)
ALTER TABLE public.recipes
  ADD COLUMN IF NOT EXISTS price_takeaway numeric,
  ADD COLUMN IF NOT EXISTS price_delivery numeric;

-- 2. config ช่องทาง (packaging + commission แก้ได้ ไม่ต้อง deploy)
CREATE TABLE IF NOT EXISTS public.pricing_channels (
  channel        text PRIMARY KEY,            -- 'dine_in' | 'takeaway' | 'delivery'
  label          text NOT NULL,
  packaging_cost numeric NOT NULL DEFAULT 0,
  commission_pct numeric NOT NULL DEFAULT 0,
  sort_order     int     NOT NULL DEFAULT 0,
  updated_at     timestamptz DEFAULT now()
);

INSERT INTO public.pricing_channels (channel, label, packaging_cost, commission_pct, sort_order) VALUES
  ('dine_in',  'หน้าร้าน', 0, 0,    1),
  ('takeaway', 'กลับบ้าน', 0, 0,    2),
  ('delivery', 'Delivery', 0, 32.1, 3)
ON CONFLICT (channel) DO NOTHING;
```

- `selling_price` = ช่อง dine_in (ไม่เปลี่ยน semantic เดิม → `list_recipes` gp_pct ทำงานต่อได้)
- `price_takeaway` / `price_delivery` = NULL หมายถึง "ยังไม่ตั้ง"
- delivery commission default 32.1 ตรงกับ `_LINEMAN_GP_RATE` เดิม

---

## 7. API (ต่อยอด `recipe_routes.py` — ไม่สร้างไฟล์ใหม่)

### `GET /recipes/{id}/pricing`
Query: `target_pct` (float, required), `mode` (`cost`|`gp`, default `cost`), `rounding` (`9`|`0`|`5`|`none`, default `9`)

Response:
```json
{
  "recipe_id": "uuid",
  "recipe_name": "หมูกระทะชุด",
  "food_cost": 42.00,
  "cost_incomplete": false,
  "missing_price_count": 0,
  "target": { "mode": "cost", "pct": 30, "rounding": "9" },
  "channels": [
    {
      "channel": "dine_in", "label": "หน้าร้าน",
      "packaging_cost": 0, "commission_pct": 0,
      "channel_cost": 42.00, "price_raw": 140.00, "suggested_price": 149,
      "net_gp_baht": 107.00, "net_gp_pct": 71.8,
      "current_price": 139, "low_margin": false
    }
  ]
}
```
- `current_price` ดึงจาก `recipes`: dine_in→`selling_price`, takeaway→`price_takeaway`, delivery→`price_delivery`
- คำนวณอย่างเดียว ไม่เขียน DB

### `PUT /recipes/{id}/prices`
Body (subset ได้): `{ "dine_in": 149, "takeaway": 159, "delivery": 159 }`
→ เขียน `selling_price` / `price_takeaway` / `price_delivery` ตามที่ส่งมา, คืน recipe ที่อัปเดต + net_gp ต่อช่องที่คิดใหม่

### `GET /pricing/channels` / `PUT /pricing/channels`
อ่าน/แก้ config `pricing_channels` (`label`, `packaging_cost`, `commission_pct`)

> Validation: `mode=cost` → `0 < target_pct < 100`; `mode=gp` → `0 < target_pct < 100`. นอกช่วง → 400.
> Decimal: cast เป็น float ก่อนคำนวณ (psycopg2 คืน NUMERIC เป็น Decimal — ดู comment ใน `_calc_cost`).
> Backend constant: `LOW_MARGIN_PCT = 40`.

---

## 8. Frontend (`VEXONHQ/app/recipes`)

- แถวเมนูแต่ละแถวเพิ่มปุ่ม **"คิดราคา"** → เปิด panel/modal `PricingPanel`
- Panel components:
  - แสดง `food_cost` (auto, อ่านอย่างเดียว) + banner เตือนถ้า `cost_incomplete`
  - target: radio (`Cost %` / `GP %`) + ช่องตัวเลข
  - dropdown ปัดเศษ (`฿9` / `฿0` / `฿5` / ไม่ปัด)
  - ตาราง 3 แถวช่องทาง: packaging | ราคาเสนอ | GP สุทธิ% | ราคาปัจจุบัน | ปุ่ม [ใช้ราคานี้]
  - ⚠ ที่แถวที่ `low_margin = true`
  - ปุ่ม "save ทั้งหมด"
- ใช้ global fetch interceptor (`components/AuthProvider.tsx`) — **ห้ามใส่ `Authorization` header รายหน้า** (กฎ MARA overview, จะพัง 401-refresh-retry)

Mockup:
```
┌─ คิดราคาขาย: หมูกระทะชุด ──────────────────────────┐
│ ต้นทุนอาหาร (auto):           42.00 ฿   [yield รวมแล้ว]│
│ เป้าหมาย:  ( • ) Cost %  ( ) GP %      [ 30 ] %        │
│ ปัดเศษ:    [ ลงท้าย ฿9 ▾ ]                              │
│              ค่ากล่อง   ราคาเสนอ   GP สุทธิ   ปัจจุบัน    │
│  หน้าร้าน      –         149 ฿      71.8%     139  [ใช้] │
│  กลับบ้าน     5 ฿        159 ฿      70.4%      –   [ใช้] │
│  Delivery     5 ฿        159 ฿      38.4% ⚠    –   [ใช้] │
│                                       [ save ทั้งหมด ]   │
└────────────────────────────────────────────────────────┘
```

---

## 9. Testing & Verify (กฎ CLAUDE.md)

- `tests/test_pricing.py` — unit test สูตร pure function:
  - forward cost/gp mode, rounding ทั้ง 4 แบบ, commission 0 vs 32%, low_margin flag, ปัดขึ้นเสมอ
  - reverse: ราคา → cost%/net_gp%
  - edge: `food_cost = 0`, `target_pct` นอกช่วง → 400
- `tests/test_smoke.py` — เพิ่ม route ใหม่ใน smoke list:
  - `GET /recipes/{id}/pricing` → 200 + มี `channels` 3 ตัว
  - `GET /pricing/channels` → 200 + 3 แถว
  - `PUT /recipes/{id}/prices` roundtrip (set แล้วอ่านกลับตรง)
- Verify column names กับ `information_schema.columns` ก่อนเขียน SQL
- `python -c "import ast; ast.parse(...)"` ทุกไฟล์ .py ที่แตะ + `.\verify.ps1`
- Backup tag ก่อน handoff (กฎ CLAUDE.md #5), แล้วส่ง diff ให้ TUM push (Claude ไม่ push เอง)

## 10. Edge cases

- `cost_incomplete = true` (วัตถุดิบยังไม่มีราคา) → คำนวณต่อได้แต่ banner เตือนว่าเลขยังไม่น่าเชื่อถือ (ใช้ flag ที่ `_calc_cost` มีอยู่)
- `food_cost = 0` → price_raw = 0, suggested = 0, เตือน "ยังไม่มีต้นทุน"
- commission สูงจน net_gp ติดลบ → โชว์เป็นสีแดง, `low_margin = true`
- หารด้วยศูนย์ → guard ทุกจุด (target_pct, price, suggested_price)
- ปัดเศษต้องไม่ทำให้ราคาต่ำกว่า price_raw (ปัดขึ้นเท่านั้น)

## 11. Future extensions (ไม่อยู่ใน v1)

- per-recipe packaging override (คอลัมน์ค่ากล่องต่อเมนู)
- channel รายแพลตฟอร์ม (Grab/LINEMAN/Robinhood แยก commission)
- ดัน suggested delivery price กลับ `/pos/prices`
- unify `_LINEMAN_GP_RATE` ใน `pos_import.py` ให้อ่านจาก `pricing_channels` (ลบ hardcode ซ้ำ)

---

## 12. Effort estimate

- Migration: ~15 นาที (additive, ปลอดภัย)
- Backend: pure-function สูตร + 4 endpoints ใน `recipe_routes.py` (~150-200 บรรทัด)
- Tests: `test_pricing.py` + เพิ่ม smoke (~80 บรรทัด)
- Frontend: `PricingPanel` component + ปุ่มใน `/recipes` (~1 ไฟล์ component)

งานเล็ก คุ้ม — reuse `_calc_cost` ที่มีอยู่, migration เป็น additive, ไม่แตะ flow เดิม
