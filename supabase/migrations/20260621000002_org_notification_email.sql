-- Add notification_email to organizations — used by the alert engine to know
-- where to send email alerts. NULL means "not yet configured"; engine skips send.
-- No RLS change needed: organizations table RLS already scopes by current_org_id().
ALTER TABLE public.organizations ADD COLUMN IF NOT EXISTS notification_email TEXT;
