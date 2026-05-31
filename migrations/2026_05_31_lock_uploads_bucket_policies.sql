-- =====================================================================
-- 2026-05-31 SECURITY AUDIT — GAP 2: lock the `uploads` storage bucket
-- =====================================================================
-- APPLIED LIVE 2026-05-31 via Supabase MCP (TUM approved). Committed for record.
--
-- WHY: the `uploads` bucket (240 objects = OCR'd bank statements / payment slips
-- / vendor invoices — bank account numbers, tax IDs, amounts) had over-permissive
-- storage.objects policies:
--   "Allow read"  (SELECT, public)  + "Allow read 1va6avm_0"  (SELECT, anon)
--   "Allow upload"(INSERT, public)  + "Allow upload 1va6avm_0"(INSERT, anon)
-- => any anonymous caller could LIST every financial document (enumerate) and
--    UPLOAD arbitrary files. Confirmed live: POST /storage/v1/object/list/uploads
--    with the anon key returned the full object list.
--
-- WHY SAFE: the backend uploads+reads via the service_role key (BYPASSRLS), so it
-- does NOT rely on these policies. The bucket stays public=true, so the OCR-review
-- dashboard (which renders stored /object/public/uploads/<uuid> URLs) keeps working.
-- Verified post-apply: public-URL download of a known object still 200; anon LIST
-- returns []; anon upload returns 400.
--
-- FOLLOW-UP (not done here — needs app changes): full private bucket + signed URLs
-- requires persisting storage_path (currently discarded) + generating signed URLs at
-- read time in the bill/slip detail endpoints + a one-time backfill of existing rows.
DROP POLICY IF EXISTS "Allow read" ON storage.objects;
DROP POLICY IF EXISTS "Allow read 1va6avm_0" ON storage.objects;
DROP POLICY IF EXISTS "Allow upload" ON storage.objects;
DROP POLICY IF EXISTS "Allow upload 1va6avm_0" ON storage.objects;
