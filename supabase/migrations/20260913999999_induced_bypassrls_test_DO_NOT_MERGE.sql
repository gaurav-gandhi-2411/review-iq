-- INDUCED FAILURE (Session 15c C3b) -- DO NOT MERGE. Exists on a throwaway branch only, to prove
-- that bypassrls-container-check goes red when review_iq_app holds BYPASSRLS. Runs solely against
-- the CI job's throwaway postgres service container; never applied to any real database.
ALTER ROLE review_iq_app BYPASSRLS;
