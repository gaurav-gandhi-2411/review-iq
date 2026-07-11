-- Migration: fix quota_requests.org_id FK to ON DELETE CASCADE.
--
-- Found 2026-07-11 while building the self-service org-deletion endpoint (audit
-- finding #7): quota_requests.org_id was ON DELETE NO ACTION, unlike every other
-- org-scoped table in this schema (organizations/extractions/usage_records/
-- batch_jobs/batch_job_rows/corrections/alert_preferences/alert_log/
-- shopify_installations/google_business_installations all cascade). NO ACTION
-- would block `DELETE FROM organizations` outright if the org had any
-- quota_requests rows. Table is currently empty (verified 2026-07-11) -- zero
-- data risk in dropping and re-adding the constraint.

ALTER TABLE public.quota_requests
  DROP CONSTRAINT quota_requests_org_id_fkey;

ALTER TABLE public.quota_requests
  ADD CONSTRAINT quota_requests_org_id_fkey
  FOREIGN KEY (org_id) REFERENCES public.organizations (id) ON DELETE CASCADE;
