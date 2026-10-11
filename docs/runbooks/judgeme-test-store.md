# Judge.me test store: runbook for GG

Date: 2026-10-11. Nothing here was run by Claude: there is no Judge.me or Shopify Partner account on this machine, and no live call to Judge.me was made. Labels: VERIFIED = stated on the cited Judge.me help page (per GG's research this session); BELIEVED = not confirmed; UNCONFIRMED = looked for, not established.

## What is and is not known about plans

- A Shopify **development store** gives free access to Judge.me's Awesome plan for testing. VERIFIED (judge.me/help/en/articles/8278390).
- Whether the **Forever Free** plan includes the REST API: UNCONFIRMED. The API help page (8409180) does not name a plan; a third-party profile says yes (BELIEVED). Do not assume it. Step 6 below tests it.
- Cheapest free alternative if the free plan lacks API access: the development-store Awesome access above. No paid plan is needed for this work. Do not pay for Judge.me to run this test.

## Steps

1. **Shopify Partner account** (free). Go to partners.shopify.com, sign up with the identity you use for this project, accept the Partner terms. No payment details are required for development stores.
2. **Development store.** Partner dashboard > Stores > Add store > Create development store > "Test an app or theme". Pick any name; the permanent domain will be `<name>.myshopify.com` (this is the `shop_domain` the connector wants). Add a few products (Shopify's "add sample data" is fine).
3. **Install Judge.me.** In the store admin open Apps > Shopify App Store > search "Judge.me Product Reviews" > Add app. Choose the free plan during onboarding, or leave the dev-store Awesome access in place. Note which plan the Judge.me admin shows (Settings > Plan), because that is the answer to R8.
4. **Seed about 50 reviews.** Judge.me has a review import (Judge.me admin > Reviews > Import/Export > Import reviews, CSV). Prepare a CSV with columns the importer asks for (title, body, rating, reviewer name, reviewer email, product handle or id, created date). Use fake names and `@example.com` emails. Mix: ratings 1-5, some very short, some 600+ characters, some Hindi/Hinglish, two with the same text, a few unpublished after import. What I can verify: the importer's exact column names and whether imported reviews appear in the API list. What I cannot verify from here: either of those; read the importer's template (the page offers one) rather than trusting this paragraph.
5. **Generate the private token.** Judge.me admin > Settings > Integrations > "View API tokens" (VERIFIED location). Copy the **private** token. Remember: it is read/write, and you cannot regenerate it yourself (support only). Treat it like a password. Do not paste it into chat, a commit, or a screenshot.
6. **Record real responses** (read-only calls):
   ```powershell
   $env:JUDGEME_API_TOKEN = "<private token>"
   $env:JUDGEME_SHOP_DOMAIN = "<name>.myshopify.com"
   C:\Users\gaura\ml-projects\review-iq\.venv\Scripts\python.exe scripts\record_judgeme_fixture.py --pages 4 --per-page 100 --out tests\fixtures\judgeme\recorded_day1.json
   Remove-Item Env:JUDGEME_API_TOKEN
   ```
   Do this on 3 different days (R2 needs three recordings). If the very first call returns 401/403 with a correct token, note the exact status and body: that is the free-plan answer for R8.
7. **Run the checklist** in `docs/specs/judgeme-ingestion.md` section 9 (R1-R10), then rerun the connector's tests against the recordings. Send the recordings (they are redacted at write time; read them before committing) and the results back; do not enable `ENABLE_JUDGEME_CONNECTOR` until the decision rules in section 5 of the spec are satisfied.
8. Later, for the end-to-end check: apply the migration file yourself (the PRs never apply it), set the two secrets, enable the flag for the test org only.
