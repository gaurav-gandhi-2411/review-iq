-- Migration: widen alert_preferences.event_type CHECK to include the two new Phase 2 detector
-- event types (batch_defect, fake_campaign). Plan approved by GG 2026-07-11 (see
-- C:\Users\gaura\.claude\plans\zany-fluttering-kahan.md).
--
-- Additive only -- no RLS policy changes, no new columns, no new tables. RLS policies on
-- alert_preferences ("alert_prefs_authenticated_all": org_id = current_org_id() on both USING
-- and WITH CHECK; "alert_prefs_anon_deny": deny all) are UNCHANGED by this migration -- verified
-- via pg_policy before/after, same proof pattern as 20260710000001_review_date.sql.
-- alert_log.event_type already has NO CHECK constraint (see 20260621000001_alerts.sql) -- needs
-- nothing.
--
-- Idempotent: DROP CONSTRAINT IF EXISTS before ADD CONSTRAINT.

ALTER TABLE public.alert_preferences DROP CONSTRAINT IF EXISTS alert_preferences_event_type_check;
ALTER TABLE public.alert_preferences ADD CONSTRAINT alert_preferences_event_type_check
    CHECK (event_type IN ('high_urgency', 'likely_fake', 'fake_cluster', 'topic_spike',
                           'batch_defect', 'fake_campaign'));
