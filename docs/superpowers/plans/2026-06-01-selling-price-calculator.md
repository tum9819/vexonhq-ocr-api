# Selling Price Calculator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** เพิ่ม forward selling-price calculator (target% → ราคา) แยก 3 ช่องทาง (หน้าร้าน/กลับบ้าน/delivery) พร้อม GP สุทธิหลังหักค่าคอม ใน VEXONHQ — ปิด gap #15 เทียบ RestoSheet Pro.

**Architecture:** Pure-function pricing module (`pricing.py`) คำนวณราคา/ปัดเศษ/GP สุทธิ — unit-test ได้โดยไม่แตะ DB. 4 endpoints ใน `recipe_routes.py` reuse `_calc_cost` + ตาราง config `pricing_channels`. Frontend ฝัง section ในเดสก์ detail panel ของ `/recipes` (ไม่ทำ modal ใหม่). Additive migration — ไม่พังโค้ดที่อ่าน `selling_price`.

**Tech Stack:** Python FastAPI + psycopg2, pytest; Next.js 16 + React 19 + Tailwind (lucide-react).

Spec: `docs/superpowers/specs/2026-06-01-selling-price-calculator-design.md`

---

## File Structure

| File | Repo | Responsibility |
|---|---|---|
| Create `pricing.py` | ocr-api | Pure functions: `round_price`, `compute_channel`, `compute_reverse`, `LOW_MARGIN_PCT` |
| Create `tests/test_pricing.py` | ocr-api | Unit tests for pure functions |
| Create `migrations/2026_06_02_selling_price_channels.sql` | ocr-api | Add `price_takeaway`/`price_delivery` cols + `pricing_channels` table + seed |
| Modify `recipe_routes.py` | ocr-api | 4 endpoints: GET pricing, PUT prices, GET/PUT pricing channels |
| Modify `tests/test_smoke.py` | ocr-api | Add new routes to smoke list |
| Modify `app/recipes/page.tsx` | VEXONHQ | Pricing section in detail panel |

> Route paths (router prefix `/recipes`): `GET /recipes/{id}/pricing`, `PUT /recipes/{id}/prices`, `GET|PUT /recipes/pricing/channels` (two-segment static path — no collision with `/{recipe_id}`).

---

## Task 1: Pure pricing module (TDD)

**Files:**
- Create: `pricing.py`
- Test: `tests/test_pricing.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pricing.py
import pytest
from pricing import round_price, compute_channel, compute_reverse, LOW_MARGIN_PCT


def test_round_price_modes():
    assert round_price(140.00, "9") == 149      # 0 -> next ...9
    assert round_price(156.67, "9") == 159
    assert round_price(149.0, "9") == 149       # already ...9
    assert round_price(156.67, "0") == 160      # up to nearest 10
    assert round_price(156.67, "5") == 160      # up to nearest 5
    assert round_price(156.67, "none") == 157   # ceil to whole baht
    assert round_price(0, "9") == 0


def test_compute_channel_dine_in_cost_mode():
    r = compute_channel(food_cost=42.0, packaging_cost=0, commission_pct=0,
                        target_pct=30, mode="cost", rounding="9")
    assert r["channel_cost"] == 42.0
    assert r["suggested_price"] == 149
    assert r["net_gp_pct"] == 71.8
    assert r["low_margin"] is False


def test_compute_channel_delivery_commission_eats_margin():
    r = compute_channel(food_cost=42.0, packaging_cost=5, commission_pct=32,
                        target_pct=30, mode="cost", rounding="9")
    assert r["suggested_price"] == 159          # (42+5)/0.30=156.67 -> 159
    assert r["net_gp_pct"] == 38.4              # (159*0.68-47)/159
    assert r["low_margin"] is True              # < 40


def test_compute_channel_gp_mode_equals_cost_complement_when_no_commission():
    a = compute_channel(42.0, 0, 0, target_pct=30, mode="cost", rounding="none")
    b = compute_channel(42.0, 0, 0, target_pct=70, mode="gp", rounding="none")
    assert a["suggested_price"] == b["suggested_price"]


def test_compute_channel_zero_cost_is_safe():
    r = compute_channel(0, 0, 0, target_pct=30, mode="cost", rounding="9")
    assert r["suggested_price"] == 0
    assert r["net_gp_pct"] == 0.0


def test_compute_channel_invalid_target_raises():
    with pytest.raises(ValueError):
        compute_channel(42.0, 0, 0, target_pct=0, mode="cost", rounding="9")
    with pytest.raises(ValueError):
        compute_channel(42.0, 0, 0, target_pct=100, mode="gp", rounding="9")


def test_compute_reverse():
    r = compute_reverse(food_cost=42.0, packaging_cost=5, commission_pct=32, price=159)
    assert r["cost_pct"] == 29.6                # 47/159
    assert r["net_gp_pct"] == 38.4
    assert r["low_margin"] is True
```

- [ ] **Step 2: Run, verify fail**

Run: `python -m pytest tests/test_pricing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pricing'`

- [ ] **Step 3: Implement `pricing.py`**

```python
"""Pure selling-price calculator — no DB, no I/O. Unit-tested in tests/test_pricing.py.

channel_cost = food_cost + packaging_cost
Forward:  price = channel_cost / (target/100)            [mode=cost]
          price = channel_cost / (1 - target/100)        [mode=gp]
Net GP after platform commission (delivery):
          net_gp_baht = price*(1-comm) - channel_cost
All rounding rounds UP to protect margin.
"""
from __future__ import annotations

import math

LOW_MARGIN_PCT = 40.0


def round_price(price_raw: float, mode: str) -> int:
    """Round UP. mode in {'9','0','5','none'}."""
    if price_raw <= 0:
        return 0
    if mode == "none":
        return math.ceil(price_raw)
    if mode == "0":
        return math.ceil(price_raw / 10) * 10
    if mode == "5":
        return math.ceil(price_raw / 5) * 5
    if mode == "9":
        base = math.ceil(price_raw)
        return base + (9 - (base % 10)) % 10
    raise ValueError(f"unknown rounding mode: {mode}")


def compute_channel(food_cost, packaging_cost, commission_pct,
                    target_pct, mode, rounding) -> dict:
    channel_cost = float(food_cost or 0) + float(packaging_cost or 0)
    comm = float(commission_pct or 0) / 100.0
    t = float(target_pct) / 100.0
    if mode == "cost":
        if not 0 < t < 1:
            raise ValueError("cost target_pct must be in (0,100)")
        price_raw = channel_cost / t
    elif mode == "gp":
        if not 0 < t < 1:
            raise ValueError("gp target_pct must be in (0,100)")
        price_raw = channel_cost / (1.0 - t)
    else:
        raise ValueError(f"unknown mode: {mode}")

    suggested = round_price(price_raw, rounding)
    if suggested > 0:
        net_gp_baht = suggested * (1 - comm) - channel_cost
        net_gp_pct = net_gp_baht / suggested * 100
    else:
        net_gp_baht = 0.0
        net_gp_pct = 0.0
    return {
        "channel_cost": round(channel_cost, 2),
        "price_raw": round(price_raw, 2),
        "suggested_price": suggested,
        "net_gp_baht": round(net_gp_baht, 2),
        "net_gp_pct": round(net_gp_pct, 1),
        "low_margin": net_gp_pct < LOW_MARGIN_PCT,
    }


def compute_reverse(food_cost, packaging_cost, commission_pct, price) -> dict:
    channel_cost = float(food_cost or 0) + float(packaging_cost or 0)
    comm = float(commission_pct or 0) / 100.0
    price = float(price or 0)
    if price <= 0:
        return {"cost_pct": 0.0, "net_gp_pct": 0.0, "low_margin": True}
    cost_pct = channel_cost / price * 100
    net_gp_pct = (price * (1 - comm) - channel_cost) / price * 100
    return {
        "cost_pct": round(cost_pct, 1),
        "net_gp_pct": round(net_gp_pct, 1),
        "low_margin": net_gp_pct < LOW_MARGIN_PCT,
    }
```

- [ ] **Step 4: Run, verify pass**

Run: `python -m pytest tests/test_pricing.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add pricing.py tests/test_pricing.py
git commit -m "feat: pricing pure-function module + unit tests"
```

---

## Task 2: Migration

**Files:** Create `migrations/2026_06_02_selling_price_channels.sql`

- [ ] **Step 1: Verify existing schema (read-only) before writing**

Confirm `recipes` has `selling_price` and NOT already `price_takeaway`/`price_delivery`:
```sql
SELECT column_name FROM information_schema.columns
WHERE table_schema='public' AND table_name='recipes';
```

- [ ] **Step 2: Write migration**

```sql
-- Selling Price Calculator: per-channel prices + channel config
ALTER TABLE public.recipes
  ADD COLUMN IF NOT EXISTS price_takeaway numeric,
  ADD COLUMN IF NOT EXISTS price_delivery numeric;

CREATE TABLE IF NOT EXISTS public.pricing_channels (
  channel        text PRIMARY KEY,
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

- [ ] **Step 3: Commit** (TUM applies SQL to Supabase before code deploy)

```bash
git add migrations/2026_06_02_selling_price_channels.sql
git commit -m "feat: migration for per-channel pricing + pricing_channels config"
```

---

## Task 3: Endpoints in recipe_routes.py

**Files:** Modify `recipe_routes.py`

- [ ] **Step 1: Add import + Pydantic models near top (after existing imports)**

```python
import pricing  # pure calculator module

class ChannelPriceUpdate(BaseModel):
    dine_in: Optional[float] = None
    takeaway: Optional[float] = None
    delivery: Optional[float] = None

class PricingChannelRow(BaseModel):
    channel: str
    label: Optional[str] = None
    packaging_cost: Optional[float] = None
    commission_pct: Optional[float] = None

_CH_PRICE_COL = {"dine_in": "selling_price", "takeaway": "price_takeaway", "delivery": "price_delivery"}
```

- [ ] **Step 2: Add the 4 routes. The two-segment static `/pricing/channels` routes MUST be declared BEFORE `@router.get("/{recipe_id}")` (line ~896) is irrelevant because path differs in segment count, but keep them grouped above `/{recipe_id}/pricing` for clarity.**

```python
@router.get("/pricing/channels")
def get_pricing_channels():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT channel, label, packaging_cost, commission_pct, sort_order
                FROM public.pricing_channels ORDER BY sort_order
            """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in rows:
                r["packaging_cost"] = float(r["packaging_cost"] or 0)
                r["commission_pct"] = float(r["commission_pct"] or 0)
        return {"channels": rows}
    finally:
        conn.close()


@router.put("/pricing/channels")
def update_pricing_channels(body: List[PricingChannelRow]):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            for row in body:
                fields = {k: v for k, v in row.dict().items()
                          if k != "channel" and v is not None}
                if not fields:
                    continue
                set_clause = ", ".join(f"{k} = %s" for k in fields)
                cur.execute(
                    f"UPDATE public.pricing_channels SET {set_clause}, updated_at = NOW() WHERE channel = %s",
                    list(fields.values()) + [row.channel],
                )
        conn.commit()
        return {"status": "updated"}
    finally:
        conn.close()


@router.get("/{recipe_id}/pricing")
def get_recipe_pricing(recipe_id: str, target_pct: float, mode: str = "cost",
                       rounding: str = "9"):
    if mode not in ("cost", "gp"):
        raise HTTPException(400, "mode must be 'cost' or 'gp'")
    if rounding not in ("9", "0", "5", "none"):
        raise HTTPException(400, "invalid rounding")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name, selling_price, price_takeaway, price_delivery "
                        "FROM public.recipes WHERE id = %s", (recipe_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Recipe not found")
            name, p_dine, p_take, p_del = row
            current = {"dine_in": p_dine, "takeaway": p_take, "delivery": p_del}

            cost_data = _calc_cost(cur, recipe_id)
            food_cost = cost_data["total_cost"]

            cur.execute("""
                SELECT channel, label, packaging_cost, commission_pct
                FROM public.pricing_channels ORDER BY sort_order
            """)
            channels = []
            for channel, label, packaging, commission in cur.fetchall():
                try:
                    calc = pricing.compute_channel(
                        food_cost, packaging, commission, target_pct, mode, rounding)
                except ValueError as e:
                    raise HTTPException(400, str(e))
                cur_price = current.get(channel)
                channels.append({
                    "channel": channel, "label": label,
                    "packaging_cost": float(packaging or 0),
                    "commission_pct": float(commission or 0),
                    **calc,
                    "current_price": float(cur_price) if cur_price is not None else None,
                })
        return {
            "recipe_id": recipe_id, "recipe_name": name,
            "food_cost": food_cost,
            "cost_incomplete": cost_data["cost_incomplete"],
            "missing_price_count": cost_data["missing_price_count"],
            "target": {"mode": mode, "pct": target_pct, "rounding": rounding},
            "channels": channels,
        }
    finally:
        conn.close()


@router.put("/{recipe_id}/prices")
def update_recipe_prices(recipe_id: str, body: ChannelPriceUpdate):
    updates = {_CH_PRICE_COL[k]: v for k, v in body.dict().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No prices to update")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            set_clause = ", ".join(f"{c} = %s" for c in updates)
            cur.execute(
                f"UPDATE public.recipes SET {set_clause}, updated_at = NOW() WHERE id = %s",
                list(updates.values()) + [recipe_id],
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "Recipe not found")
        conn.commit()
        return {"status": "updated", "updated": list(updates.keys())}
    finally:
        conn.close()
```

- [ ] **Step 3: Syntax check**

Run: `python -c "import ast; ast.parse(open('recipe_routes.py', encoding='utf-8').read())"`
Expected: no output (parses)

- [ ] **Step 4: Commit**

```bash
git add recipe_routes.py
git commit -m "feat: selling-price calculator endpoints (pricing, prices, channels)"
```

---

## Task 4: Smoke tests

**Files:** Modify `tests/test_smoke.py`

- [ ] **Step 1: Add new GET routes to the smoke route list** (read the file first; append `/recipes/pricing/channels` to the GET list, and a guarded test that fetches `/recipes` then hits `/recipes/{first_id}/pricing?target_pct=30`).

```python
def test_recipe_pricing_smoke():
    import requests, os
    base = os.environ.get("BACKEND_URL", "https://api.marastation.com")
    token = _login_token()  # reuse existing helper if present
    h = {"Authorization": f"Bearer {token}"}
    chans = requests.get(f"{base}/recipes/pricing/channels", headers=h, timeout=15)
    assert chans.status_code == 200
    assert len(chans.json()["channels"]) >= 1
    recipes = requests.get(f"{base}/recipes", headers=h, timeout=15).json()["recipes"]
    if recipes:
        rid = recipes[0]["id"]
        p = requests.get(f"{base}/recipes/{rid}/pricing?target_pct=30", headers=h, timeout=15)
        assert p.status_code == 200
        assert len(p.json()["channels"]) >= 1
```

> Match the existing auth/helper style in `tests/test_smoke.py` when wiring `_login_token()`.

- [ ] **Step 2: Commit**

```bash
git add tests/test_smoke.py
git commit -m "test: smoke coverage for pricing endpoints"
```

---

## Task 5: Frontend — pricing section in /recipes detail panel

**Files:** Modify `app/recipes/page.tsx` (VEXONHQ repo)

> Pattern: plain `fetch(\`${API_URL}/...\`)` NO manual Authorization header (global AuthProvider interceptor handles it — matches every other fetch in this file). Reuse `fmt()`, add a section inside the `detail` panel after the cost summary.

- [ ] **Step 1: Add types + state**

```tsx
type PricingChannel = {
  channel: string; label: string; packaging_cost: number; commission_pct: number;
  channel_cost: number; price_raw: number; suggested_price: number;
  net_gp_baht: number; net_gp_pct: number; low_margin: boolean;
  current_price: number | null;
};
type PricingResp = {
  recipe_id: string; recipe_name: string; food_cost: number;
  cost_incomplete: boolean; missing_price_count: number;
  target: { mode: string; pct: number; rounding: string };
  channels: PricingChannel[];
};
// inside component:
const [pricing, setPricing] = useState<PricingResp | null>(null);
const [pcMode, setPcMode] = useState<'cost' | 'gp'>('cost');
const [pcPct, setPcPct] = useState(30);
const [pcRounding, setPcRounding] = useState<'9' | '0' | '5' | 'none'>('9');
const [pcSaving, setPcSaving] = useState('');
```

- [ ] **Step 2: Add fetch + save functions**

```tsx
async function loadPricing(id: string) {
  const res = await fetch(`${API_URL}/recipes/${id}/pricing?target_pct=${pcPct}&mode=${pcMode}&rounding=${pcRounding}`);
  if (!res.ok) { setPricing(null); return; }
  setPricing(await res.json());
}
async function savePrice(channel: string, price: number) {
  if (!detail) return;
  setPcSaving(channel);
  try {
    await fetch(`${API_URL}/recipes/${detail.id}/prices`, {
      method: 'PUT', headers, body: JSON.stringify({ [channel]: price }),
    });
    await loadPricing(detail.id);
    await load();
  } finally { setPcSaving(''); }
}
```

- [ ] **Step 3: Recompute when a recipe is selected or knobs change**

In `loadDetail`, after `setDetail(data)`, call `loadPricing(id)`. Add:
```tsx
useEffect(() => { if (detail) loadPricing(detail.id); /* eslint-disable-next-line */ }, [pcMode, pcPct, pcRounding]);
```

- [ ] **Step 4: Render the section** (insert in detail panel, after the cost-summary grid ~line 645)

```tsx
{pricing && (
  <div className="mb-5 rounded-lg border border-indigo-200 bg-indigo-50/40 p-3">
    <h3 className="font-semibold text-gray-700 mb-2 text-sm">💰 คิดราคาขาย</h3>
    <div className="flex flex-wrap items-center gap-3 mb-3 text-xs">
      <div className="flex items-center gap-1">
        <button onClick={() => setPcMode('cost')} className={`px-2 py-1 rounded ${pcMode==='cost'?'bg-indigo-600 text-white':'bg-white border'}`}>Cost %</button>
        <button onClick={() => setPcMode('gp')} className={`px-2 py-1 rounded ${pcMode==='gp'?'bg-indigo-600 text-white':'bg-white border'}`}>GP %</button>
      </div>
      <input type="number" value={pcPct} onChange={e => setPcPct(+e.target.value)}
        className="w-20 border rounded px-2 py-1" /> <span>%</span>
      <select value={pcRounding} onChange={e => setPcRounding(e.target.value as typeof pcRounding)} className="border rounded px-2 py-1">
        <option value="9">ลงท้าย ฿9</option><option value="0">฿0</option><option value="5">฿5</option><option value="none">ไม่ปัด</option>
      </select>
    </div>
    <table className="w-full text-xs">
      <thead className="text-gray-500"><tr>
        <th className="text-left py-1">ช่องทาง</th><th className="text-right">ค่ากล่อง</th>
        <th className="text-right">ราคาเสนอ</th><th className="text-right">GP สุทธิ</th>
        <th className="text-right">ปัจจุบัน</th><th></th>
      </tr></thead>
      <tbody>
        {pricing.channels.map(c => (
          <tr key={c.channel} className="border-t">
            <td className="py-1.5">{c.label}{c.commission_pct>0 && <span className="text-gray-400"> · คอม {c.commission_pct}%</span>}</td>
            <td className="text-right">{c.packaging_cost>0?`฿${fmt(c.packaging_cost)}`:'–'}</td>
            <td className="text-right font-semibold">฿{fmt(c.suggested_price)}</td>
            <td className={`text-right font-bold ${c.low_margin?'text-red-500':'text-emerald-600'}`}>{c.net_gp_pct}%{c.low_margin && ' ⚠'}</td>
            <td className="text-right text-gray-500">{c.current_price!=null?`฿${fmt(c.current_price)}`:'–'}</td>
            <td className="text-right">
              <button onClick={() => savePrice(c.channel, c.suggested_price)} disabled={pcSaving===c.channel}
                className="text-indigo-600 hover:underline disabled:opacity-50">{pcSaving===c.channel?'...':'ใช้'}</button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
    {pricing.cost_incomplete && <p className="mt-2 text-[11px] text-amber-700">⚠️ ต้นทุนยังไม่ครบ ({pricing.missing_price_count} วัตถุดิบไม่มีราคา) — ราคาเสนออาจคลาดเคลื่อน</p>}
  </div>
)}
```

- [ ] **Step 5: Verify**

Run (VEXONHQ repo): `npm run lint && npx tsc --noEmit && npm run build`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add app/recipes/page.tsx
git commit -m "feat: selling-price calculator section in recipes detail panel"
```

---

## Self-Review notes
- Spec coverage: §4 formula → Task 1; §5 rounding → Task 1 `round_price`; §6 data model → Task 2; §7 API → Task 3; §8 frontend → Task 5; §9 tests → Task 1+4. ✓
- Route collision: `/recipes/pricing/channels` (2 seg) vs `/{recipe_id}` (1 seg) vs `/{recipe_id}/pricing` (2 seg, 2nd seg fixed `pricing`) — no overlap. ✓
- Frontend auth: plain fetch, no manual header (matches file). ✓
- Deploy order: migration SQL applied to Supabase BEFORE backend code deploy (else new columns 500). Stated in handoff. ✓
