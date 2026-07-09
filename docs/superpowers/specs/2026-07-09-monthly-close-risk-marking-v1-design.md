# Monthly Close Risk Marking V1 Design

Date: 2026-07-09
Owner: TUM
Status: Draft for Claude Code implementation

## Goal

Add a minimal monthly-close risk marker so accounting issues are visible during the month-close workflow instead of being discovered months later.

This V1 must stay small:

- Read-only risk detection only.
- No automatic reclassification.
- No P&L v2 migration.
- No month locking.
- No full payout reconciliation engine.
- No LINE spam.

## Existing System To Reuse

Backend:

- `menu_routes.py` already exposes `GET /alerts/summary`.
- `line_bot_routes.py` already has LINE push helpers.
- `pos_import.py` already tracks imports in `public.pos_imports`.
- `phase12_bank_statement_routes.py` already uploads bank statements, stores rows in `public.bank_statement_entries`, and uses `match_status`.
- Bank rows use `match_status IN ('needs_review', 'auto', 'manual')`.
- POS/platform import report types include `bank_statement`, `grab_transaction`, and `lineman_daily`.

Frontend:

- `app/alerts/page.tsx` already renders alert cards from `/alerts/summary`.
- Add one new alert type instead of creating a new dashboard.

Confirmed accounting rule:

- POS payment type is sales/channel evidence.
- Bank statement rows are settlement/cash-movement evidence.
- Do not treat statement text `LINE PAY` as LINE MAN sales by default.

## V1 Architecture

Use a small persistent table so the system can remember:

- which risks are currently open,
- which risks were resolved,
- which risks were ignored,
- when LINE was last sent for each critical risk.

Do not compute everything only in memory, because that cannot support 24-hour LINE throttling or audit trail.

## Database

Create table `public.monthly_close_risks`.

Columns:

```sql
id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
branch_code text NOT NULL,
month text NOT NULL,
risk_key text NOT NULL,
severity text NOT NULL CHECK (severity IN ('danger', 'warning', 'info')),
status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'ignored')),
title text NOT NULL,
message text NOT NULL,
amount numeric NOT NULL DEFAULT 0,
evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
link text NOT NULL,
first_seen_at timestamptz NOT NULL DEFAULT now(),
last_seen_at timestamptz NOT NULL DEFAULT now(),
resolved_at timestamptz NULL,
resolved_by text NULL,
ignored_at timestamptz NULL,
ignored_by text NULL,
last_line_sent_at timestamptz NULL,
created_at timestamptz NOT NULL DEFAULT now(),
updated_at timestamptz NOT NULL DEFAULT now(),
UNIQUE (branch_code, month, risk_key)
```

Notes:

- `risk_key` is one row per risk type per branch/month, not one row per affected transaction.
- Store affected row ids, counts, sums, and examples inside `evidence`.
- Limit every array/list inside `evidence` to at most 10 items.
- RLS should be enabled with no public policy, following the existing backend-service-role pattern.

Allowed `risk_key` literals for V1:

- `bank_needs_review`
- `bank_rider_income`
- `missing_platform_export_grab`
- `missing_platform_export_lineman`
- `ambiguous_settlement`
- `duplicate_statement`

## Backend API

Add a small router, for example `monthly_close_routes.py`.

Security:

- Protect all new monthly-close endpoints with the existing standard admin authentication.
- In this backend that means importing and using `auth_routes._require_admin_role` with FastAPI `Depends`.
- Unauthenticated or non-admin requests must return 401/403 according to the existing auth helper behavior.

### `POST /monthly-close/check`

Query parameters:

- `month`: required `YYYY-MM`
- `branch_code`: optional, default `thawi_watthana`

Behavior:

1. Validate month format.
2. Run V1 risk checks.
3. Upsert current risks into `monthly_close_risks`.
4. Mark previously open risks for the same branch/month as `resolved` when they are no longer present.
5. Send LINE only for open `danger` risks when:
   - `last_line_sent_at IS NULL`, or
   - `last_line_sent_at < now() - interval '24 hours'`
6. Update `last_line_sent_at` only after LINE push succeeds.
7. Return counts and risk list.

The risk check must never mutate bank statement or POS data.

### `GET /monthly-close/risks`

Query parameters:

- `month`: required `YYYY-MM`
- `branch_code`: optional, default `thawi_watthana`
- `status`: optional, default `open`

Returns risks plus counts by severity/status.

No ignore/dismiss endpoint in V1. Keep `ignored_at` and `ignored_by` in the schema for a later owner-approved workflow, but do not implement any ignore UI or API yet.

## Trigger Strategy

Do not run monthly-close checks synchronously inside import/upload transaction paths.

Accepted V1 options:

1. Manual button calls `POST /monthly-close/check`.
2. Frontend calls `POST /monthly-close/check` after a successful import/upload response.
3. Backend schedules a fire-and-forget background task after successful import/upload.

Preferred first implementation:

- Implement manual endpoint first.
- If low risk, add frontend post-upload call later.
- The risk checker failing must not make POS import or bank statement upload fail.

## Alerts Integration

Update `menu_routes.py` `/alerts/summary`:

- Include open `monthly_close_risks`.
- Map directly:
  - `severity='danger'` -> alert severity `danger`
  - `severity='warning'` -> alert severity `warning`
  - `severity='info'` -> alert severity `info`
- Alert type: `monthly_close`
- Use table `title`, `message`, `amount`, `link`.

Update frontend `app/alerts/page.tsx`:

- Add `monthly_close` to the alert type union.
- Add label: `ปิดเดือน`
- Add icon, preferably `CalendarCheck` or `ShieldAlert` from lucide-react.

Do not create a new large page in V1 unless required by review.

## LINE Policy

LINE sends only critical/danger monthly close risks.

Rules:

- Never send LINE for warning/info.
- Send first time a danger risk opens.
- If still open, send again at most once per 24 hours.
- If LINE push fails, do not update `last_line_sent_at`.
- LINE message should be short and actionable:

```text
Monthly Close Critical Risk
เดือน: 2026-07
สาขา: thawi_watthana
- Statement รอจัดหมวด 8 รายการ / ฿12,345
- POS มี Grab แต่ยังไม่มี Grab export
เปิดดู: /alerts
```

## V1 Risk Rules

### R1: Bank Statement Needs Review

Severity: `danger`

Condition:

- `bank_statement_entries.match_status = 'needs_review'`
- `txn_date` is within selected month
- branch matches

Evidence:

- count
- sum of absolute amount
- up to 10 example ids/descriptions

Reason:

- P&L and cash movement may be incomplete until these rows are reviewed.

### R2: Bank Statement Still Classified As Rider Income

Severity: `danger`

Condition:

- `bank_statement_entries.source_type IN ('rider_income_grab', 'rider_income_lineman')`
- `txn_date` is within selected month
- branch matches

Evidence:

- count
- sum credit/debit
- up to 10 affected ids/descriptions

Reason:

- Historical cleanup showed bank rows should be settlement/cash movement, not rider sales evidence.

### R3: POS Shows Delivery Channel But Platform Export Missing

Severity: `danger`

Condition:

- POS bill/payment evidence comes from `public.pos_bills.payment_type_raw`.
- In selected month, `public.pos_bills` shows:
  - `payment_type_raw = 'K Plus shop'` means Grab exists, or
  - `payment_type_raw = 'Line Man - Rabbit Linepay'` means LINE MAN exists
- but `pos_imports` has zero successful import for the corresponding platform in the selected month:
  - Grab: `report_type='grab_transaction'`
  - LINE MAN: `report_type='lineman_daily'`
- Use `public.pos_bills.sales_date` for month filtering.
- Use `public.pos_bills.branch_code` for branch filtering.
- Use `public.pos_bills.net_total` for POS amount evidence if available.

Important V1 limitation:

- This is a month-level sanity check only.
- Do not attempt daily completeness validation in V1.
- This `danger` severity depends on TUM's confirmed current POS mapping that `K Plus shop` means Grab for this bill-detail export. If that POS meaning changes to generic storefront QR later, downgrade this rule before enabling LINE for it.

Evidence:

- platform
- POS count/sum
- matching platform import count

### R4: Ambiguous Settlement Keywords

Severity: `warning`

Condition:

- selected month bank statement description contains one of:
  - `LINE PAY`
  - `Thai Line Pay`
  - `บจก. แกร็บแท็กซี่`
- and the row is not already classified as a known settlement/payout source.

Use this explicit safe filter:

```sql
AND NOT (
  match_status = 'manual'
  AND source_type IN ('grab_payout', 'lineman_payout', 'payment_gateway_payout')
  AND category_code IN ('delivery_grab', 'delivery_lineman', 'payment_gateway')
)
AND COALESCE(source_type, '') NOT IN ('grab_payout', 'lineman_payout', 'payment_gateway_payout')
```

This means already-reviewed payout rows are not flagged, while unresolved or old income-like rows are still visible.

Reason:

- Statement text alone is settlement evidence, not sales channel evidence.
- This should show on web only, not LINE.

### R5: Duplicate Statement Rows

Severity: `warning`

Condition:

- Duplicate groups within selected month by:
  - `txn_date`
  - `description`
  - `debit`
  - `credit`
  - `balance`
  - `branch_code`

Evidence:

- duplicate group count
- duplicate row count
- total duplicate amount
- up to 10 duplicate group examples

Note:

- Exact duplicates should already be prevented by constraints, but this check catches historical or edge-case pollution.

## Explicitly Deferred From V1

Do not implement these in V1:

- Missing payout within the same month.
- Rolling 3-day cross-month payout reconciliation.
- Daily platform completeness.
- Auto-reclassifying any bank row.
- Month locking or approval workflow.
- P&L v2 or rider metric redesign.
- Export footnotes.
- New large monthly-close dashboard.

Reason:

- Same-month payout checks can false-positive at month boundaries because platform payout can land in the next month.
- V1 should detect obvious month-close risks without creating noisy accounting alerts.

## Tests

Backend tests should cover:

- Month format validation rejects invalid month.
- R1 creates one monthly risk row with evidence count/sum.
- R2 creates danger risk for bank rider income rows.
- R3 creates danger only when POS delivery evidence exists and corresponding platform import count is zero.
- R3 does not create risk when no POS delivery evidence exists.
- R4 creates warning and does not trigger LINE.
- R5 creates warning for duplicates.
- Idempotency: calling `POST /monthly-close/check` twice in a row does not duplicate rows and sends LINE only once inside the 24-hour cooldown.
- Existing risk becomes resolved when no longer present.
- Re-opening: a risk goes `open` -> `resolved` -> `open`, and the existing `last_line_sent_at` still prevents LINE spam inside the 24-hour cooldown.
- LINE sends for danger first time.
- LINE does not resend within 24 hours.
- LINE resends after 24 hours if risk remains open.
- Failed LINE push does not update `last_line_sent_at`.
- Auth guard: monthly-close endpoints return 401/403 for unauthorized or non-admin requests.
- `/alerts/summary` includes open monthly close risks.

Frontend tests/checks:

- `/alerts` renders `monthly_close` alert type without TypeScript errors.
- Existing alert types still render.

## Verification

Before asking TUM to push:

Backend:

- Run syntax/targeted tests for new router and changed routes.
- Run relevant monthly-close unit tests.
- If route registration changes, smoke `GET /health/deep`.

Frontend:

- `npm run lint`
- `npx tsc --noEmit`
- `npm run build`

Manual read-only verification:

- Call `POST /monthly-close/check?month=2026-07`.
- Call `GET /monthly-close/risks?month=2026-07`.
- Call `GET /alerts/summary` and confirm monthly close risks appear.

## Claude Code Implementation Boundaries

Claude Code should implement only the V1 scope above.

Ask TUM before:

- Adding month lock/approval state.
- Adding any auto-reclass behavior.
- Adding a new large frontend page.
- Changing P&L views.
- Changing existing import semantics.
- Sending LINE warning/info.

## Antigravity Review Focus

Ask Antigravity to review:

- Risk rules for false positives/noise.
- Whether DB writes are limited to `monthly_close_risks`.
- Whether import/upload paths cannot fail because of monthly-close risk checks.
- Whether LINE throttling is correct.
- Whether the design avoids treating bank statement text as sales evidence.
- Whether V1 scope stayed small.
