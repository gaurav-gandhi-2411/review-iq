-- Migration: deduplicate authenticity_audits + add UNIQUE (org_id, review_hash).
--
-- Root-cause fix: save_authenticity_audit_pg now uses ON CONFLICT DO NOTHING (writer fix
-- applied before this migration). This migration handles existing data only.
--
-- Blast radius (confirmed read-only before apply): 1 row deleted.
-- The row to be removed is a duplicate of hash d2cad640... for org 05bbf67b...,
-- both rows score=0.880 label=genuine; the OLDER row (2026-06-13 12:47:32 UTC) is deleted,
-- keeping the NEWER row (2026-06-13 14:19:00 UTC).
--
-- ADD CONSTRAINT does not support IF NOT EXISTS in Postgres; guard via pg_constraint DO block.
-- Idempotent: DELETE on no-dup table is a no-op; DO block skips if constraint already exists.

-- (No BEGIN/COMMIT here: supabase/push.py already runs this file as one transaction together
-- with its postconditions and ledger row; an inner COMMIT would end that transaction early.
-- Removed 2026-09-20 -- already applied everywhere, so this is a comment-level change only.)

-- Step 1: remove duplicate rows (keep newest per org_id + review_hash)
DELETE FROM public.authenticity_audits
WHERE id NOT IN (
    SELECT DISTINCT ON (org_id, review_hash) id
    FROM public.authenticity_audits
    ORDER BY org_id, review_hash, created_at DESC
);

-- Step 2: add UNIQUE constraint (guarded — ADD CONSTRAINT has no IF NOT EXISTS)
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname     = 'authenticity_audits_org_review_hash_unique'
          AND conrelid    = 'public.authenticity_audits'::regclass
    ) THEN
        ALTER TABLE public.authenticity_audits
            ADD CONSTRAINT authenticity_audits_org_review_hash_unique
            UNIQUE (org_id, review_hash);
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: authenticity_audits_org_review_hash_unique
-- SQL: SELECT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid =
-- SQL: 'public.authenticity_audits'::regclass AND c.conname =
-- SQL: 'authenticity_audits_org_review_hash_unique' AND c.contype = 'u' AND
-- SQL: pg_get_constraintdef(c.oid) = 'UNIQUE (org_id, review_hash)')
