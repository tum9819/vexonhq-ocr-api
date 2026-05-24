-- Migration 0001: user_page_config
-- Session 40, 2026-05-25
-- Stores per-page visibility config for User role (Admin sees everything, no filter).

CREATE TABLE IF NOT EXISTS public.user_page_config (
  id           uuid         DEFAULT gen_random_uuid() PRIMARY KEY,
  page_href    text         UNIQUE NOT NULL,
  page_label   text         NOT NULL,
  user_visible boolean      DEFAULT false,
  sort_order   int          DEFAULT 99,
  updated_at   timestamptz  DEFAULT now()
);

-- Seed: 4 default pages visible to users
INSERT INTO public.user_page_config (page_href, page_label, user_visible, sort_order) VALUES
  ('/dashboard',         'Dashboard',                       true,  1),
  ('/pnl',               'P&L',                             true,  2),
  ('/inventory',         'Inventory (Stock)',                true,  3),
  ('/pos/prep-forecast', 'เตรียมของ 7 วัน (Focus 7 days)', true,  4)
ON CONFLICT (page_href) DO NOTHING;
