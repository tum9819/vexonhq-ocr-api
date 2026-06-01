-- Selling Price Calculator: per-channel prices + channel config
-- Closes RestoSheet gap #15. Additive + idempotent (safe to re-run).
-- Apply to Supabase BEFORE deploying the backend code that reads these columns.

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
