-- Add notification_email to organizations — used by the alert engine to know
-- where to send email alerts. NULL means "not yet configured"; engine skips send.
-- No RLS change needed: organizations table RLS already scopes by current_org_id().
ALTER TABLE public.organizations ADD COLUMN IF NOT EXISTS notification_email TEXT;

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: organizations_notification_email_column
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.organizations'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname,
-- SQL: format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('notification_email', 'text',
-- SQL: false))) = 1
