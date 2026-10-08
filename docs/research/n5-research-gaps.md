# N5: closing the two research gaps (Reddit demand evidence; marketplace review-language sample)

Date: 2026-10-08. Public pages only. No login, no captcha solving, no proxy rotation, no reviewer
names stored. Follows `docs/research/demand-evidence.md` (M2b) and `docs/research/uae-arabizi-sample.md`
(M2d), which are on PRs not yet merged to `main` at the time of writing.

Label key: VERIFIED = I fetched it and read it in this pass. BELIEVED = inference or memory, not
checked here.

## Bottom line

1. N5a (Reddit): a search WORKS through Reddit's public Atom/RSS endpoints, not through the endpoints
   we were told to try. 1,512 distinct posts were pulled and triaged; about 24 post bodies and 6 comment
   threads were read in full. Result: the pain is real for fake/hostile reviews and for "I am tired of reading
   reviews manually", and there is one Hinglish-writing Indian D2C founder who cannot separate
   product feedback from delivery noise. It is a handful of quotes, not a market. See the table.
2. N5b (sampling): Flipkart and Amazon.in review text is reachable headless without login; Amazon.ae
   too; Noon is not. In India samples, Hinglish was 0 of 50 (Flipkart) and 0 of 16 (Amazon.in). Both
   samples are almost entirely English. This is the second independent source (after the App Store
   substitute) saying romanised Hindi is rare on the marketplaces' own review pages, so the Hinglish wedge is
   weakly supported by prevalence data. UAE: Arabizi 0/50, Hinglish 0/50, Arabic script 2/50.
3. Neither result is a refutation of Hinglish demand. Sort order, sample size and categories bias the
   samples toward English (details below).

## N5a: Reddit

### What each attempt returned (exact)

| # | Request | Result |
|---|---------|--------|
| 1 | `old.reddit.com/r/IndianEcommerce/search.json?q=reviews&restrict_sr=1` | HTTP 302, `Location: old.reddit.com/login/?reason=lor2&dest=...` (login wall). Following it returns the 200 login page, no data |
| 2 | `old.reddit.com/r/IndianEcommerce/search?q=reviews&restrict_sr=1` (HTML) | HTTP 302 to the same login page |
| 3 | `www.reddit.com/r/IndianEcommerce/search.json?...` | HTTP 403 "Blocked" (HTML block page, ~190 KB) |
| 4 | `www.reddit.com/r/IndianEcommerce/about.json` | HTTP 403 "Blocked" |
| 5 | `www.reddit.com/r/<sub>/search.rss?q=...&restrict_sr=1&sort=new&limit=100` | HTTP 200, Atom XML with entries. Rate-limited: HTTP 429 after a few requests at 1.2 s spacing; header `x-ratelimit-remaining: 0`, reset ~4 s; stable at 6 s spacing with 15 s back-off on 429 |
| 6 | `www.reddit.com/r/<sub>/new.rss?limit=100` | HTTP 200, 100 newest posts per subreddit |
| 7 | `www.reddit.com/r/<sub>/comments/<id>/<slug>/.rss` | HTTP 200 with the comment tree for 6 of 10 threads tried; the other 4 returned 429 |

Deviation to flag: the instruction named `.json` endpoints on old.reddit. Both are closed (rows 1-4). Rows 5-7 use
Reddit's documented public Atom feeds, anonymous, descriptive User-Agent
(`review-iq-research/0.1 ... contact`), no login, no cookie, no IP rotation. I judged that this is not
circumvention, but it is not one of the two endpoints named, so GG should confirm it is acceptable before the
quotes are used externally. When my first pass was 429-limited I slowed down rather than retrying harder.
My first pass sent 1.2 s spacing and drew many 429s; that is within the stated "at most 1 req/s" but the
real limit is lower, and the second pass at 6 s was clean.

### Coverage

- Subreddits: r/FulfillmentByAmazon, r/indianstartups, r/Flipkart, r/IndiaBusiness, r/ecommerce, r/shopify.
  r/IndianEcommerce returned HTTP 200 with zero entries for every query and for `new.rss`; its `about.json` is 403.
  I could not tell whether the subreddit is private, quarantined, renamed or empty. Zero threads read there.
- Queries (each per subreddit, search.rss, newest first, limit 100): "review management", "Hinglish reviews",
  "customer reviews analysis", "negative reviews", "fake reviews", "respond to reviews", "review tool", plus
  the `new` feed. Some requests still ended in 429 or a DNS error after retries, so the matrix is not complete.
  I did not log per-query hit counts, only per-subreddit totals.
- Distinct posts retrieved: 1,512 (dates 2015-12 to 2026-10-07); FulfillmentByAmazon 373, ecommerce 325, shopify 325,
  indianstartups 210, IndiaBusiness 161, Flipkart 118.
- Reading depth: all 1,512 titles/snippets triaged by keyword; 279 matched a review-related keyword (loosely);
  I read the full post body of about 24 threads and the comment trees of 6 (`1sjc6go`, `1mzu0me`, `1wted7i`,
  `18b9pf5`, `1n7o5q1`, `1tnr5y4`). Search RSS returns posts only, not comments, so most comment text was
  never seen. Comments from threads outside those six are unread.
- A keyword scan for Hinglish-written posts (3+ common romanised-Hindi words) over all 1,512 posts found 4,
  of which 2 mention reviews/ratings (the same Blinkit/Zepto post cross-posted to two subs, and a subscription
  post that is not about reviews).

### Quotes (first-person, verbatim, short)

Pain 1: Hinglish / code-mixed analysis. Evidence is thin: one writer, not a statement about Hinglish reviews.

| Who | Quote | Link, date |
|-----|-------|-----------|
| Self-described owner of a small D2C snack brand on Blinkit/Zepto, writing in Hinglish | "I'm trying to read the reviews, but 90% of them are about 'late delivery' or 'rider bhaiya'. Actual product feedback kaise filter karu?" | https://www.reddit.com/r/indianstartups/comments/1mzu0me/ (also r/IndiaBusiness/1mzu4ry), 2025-08-25 |

Reading: this is a need to separate product feedback from delivery/rider noise on quick-commerce reviews, and
it is posed by someone who writes Hinglish. It says nothing about the reviews being Hinglish. The only reply
read (2025-08-26) is a vendor pitch (Dcluttr), which shows the problem has at least one competitor on
quick-commerce.

Pain 2: manual reading.

| Who | Quote | Link, date |
|-----|-------|-----------|
| "New to D2C", selling on Amazon/Flipkart | "I am new to D2C business and tiered of reading reviews manually." (asks how many hours a month and whether tools exist) | https://www.reddit.com/r/IndiaBusiness/comments/1tnr5y4/, 2026-05-26 |
| FBA seller, 1,700+ SKUs (US or unspecified, not shown to be India) | "I am looking for a solution for tracking all of my new reviews... be alerted about negative reviews." | https://www.reddit.com/r/FulfillmentByAmazon/comments/18b9pf5/, 2023-12-05 |

Counter-signal on the first thread: the top reply says to skip paid tools and "just dump all your reviews into
Claude or ChatGPT" for a summary of complaints and praise, and names Jungle Scout and Helium 10 as
aggregators. That is the cheap substitute the product has to beat. The replies read are two (3 entries including the post).

Pain 3: responding.

| Who | Quote | Link, date |
|-----|-------|-----------|
| Amazon brand owner | "I want to respond as the brand owner directly to the product review on the listing, but I don't see the option to do so." | https://www.reddit.com/r/FulfillmentByAmazon/comments/1wted7i/, 2026-09-29 |
| Same thread, repliers | Amazon "took away the ability to respond to product reviews years ago" (paraphrase of two replies; a third says it shows up for some accounts) | same thread |
| Indian small-business posters (restaurants, clinics, salons; Google/JustDial, not marketplaces) | Several founders propose paid reply-management services at INR 499 to 2,500 per month and ask whether owners care. These are builders asking, not owners complaining | e.g. r/indianstartups/1t5lq8b, 2026-05-06; r/IndiaBusiness/1sunamg, 2026-04-24 |

Reading: on marketplaces the response channel is itself constrained (Amazon), so "responding" is a weak
wedge for Amazon and Flipkart. It is stronger on Google Business Profile, which is a different product.

Pain 4: fake / hostile reviews. This is the strongest pain, and mostly not India-marketplace.

| Who | Quote | Link, date |
|-----|-------|-----------|
| Indian founder, supplement brand on Amazon | "the customers who didn't like it made sure to leave 1-star reviews on Amazon. The average rating eventually dropped to 3.6, and because of that, we decided not to manufacture the next batch." | https://www.reddit.com/r/IndiaBusiness/comments/1wos0dj/, 2026-09-24 (post body read; comment fetch hit 429) |
| Indian buyer/founder, about boAt on Amazon | "I've always considered myself pretty good at detecting fake reviews... all of them are fake. Yes, all." | https://www.reddit.com/r/indianstartups/comments/1n7o5q1/, 2025-09-03 |
| Same thread, a commenter | "amazon and nykaa are fckng filled with paid reviews!!!" (claims to have worked in influencer marketing) | same thread, 2025-09-08 |
| Amazon seller (supplements, US-style) | "legitimate VERIFIED PURCHASE 5-star reviews keep getting removed meanwhile multiple 1-star reviews suddenly appeared" | https://www.reddit.com/r/FulfillmentByAmazon/comments/1tc30ks/, 2026-05-13 |
| Amazon seller | "One 1-star Vine review completely killed my sales overnight." | https://www.reddit.com/r/FulfillmentByAmazon/comments/1ss70jc/, 2026-04-22 |
| Amazon seller | "It seems like it's been hit by a wave of negative reviews all at once... a targeted 'review bomb'" | https://www.reddit.com/r/FulfillmentByAmazon/comments/1kwuyon/, 2025-05-27 |
| Commenter in a fake-1-star thread | "The biggest problem is the detection lag. By the time you notice sales dropped 40%, the reviews have been up for days." | https://www.reddit.com/r/FulfillmentByAmazon/comments/1sjc6go/, 2026-04-13 |
| Indian shop owner (relative's shop), Google Maps | "review bombing my relative's shop with fake Google accounts" (title) | https://www.reddit.com/r/IndiaBusiness/comments/1wrmpnq/, 2026-09-27 |

The 1sjc6go thread also contains self-promotion by tool builders (a fake-review API, a listing-analysis tool),
so tools for this pain already exist and are being marketed in the same threads. 14 entries were in that thread.

### What this does and does not show

- VERIFIED: Indian-posted first-person content exists for manual review reading (1), product-vs-delivery noise
  in a Hinglish post (1), rating damage from 1-star reviews on Amazon (1), and suspected fake reviews on a named brand (1).
- VERIFIED: Fake/hostile review pain is common and repeated in r/FulfillmentByAmazon in 2025-2026 (most
  posters are not stated to be in India).
- NOT FOUND after a working search: any first-person statement that Hinglish or code-mixed reviews are unreadable
  by tools; any Indian seller saying replying to marketplace reviews is a burden. Absence is bounded by: search
  RSS has few results per query, comments mostly unread, r/IndianEcommerce unreachable, and Quora/LinkedIn
  not covered. Per rule 101a this is "checked these subs and queries, found nothing", not "confirmed absent".
- Many hits are builders asking other people whether the pain exists (survey posts). Those are not evidence of
  demand; I excluded them from the quotes except where noted.
- BELIEVED: the existing substitutes (ChatGPT/Claude paste, Helium 10, Jungle Scout) mean the manual-reading
  pain is cheap to relieve for small catalogues; the opportunity, if any, is at volume or in Indian-language handling.

## N5b: can Playwright read review pages (sampling only)

### Statement of scope

A one-time sample of about 50 public review texts per site to estimate language prevalence is a different act
from scraping review pages as a product feature. I did the first and we are not building the second. I
believe both Amazon's Conditions of Use and Flipkart's Terms prohibit automated data extraction, so a product
that scrapes marketplace review pages would breach them; I did not read that wording. Amazon's
Conditions of Use page returned HTTP 403 to `curl` and I did not push further. The Flipkart URL I tried
(`/pages/terms`) returned a search-results shell, not the Terms text, so no ToS wording was read for either site. BELIEVED, not verified here. A reviewer text sample is also not stored in the
repo; no reviewer names, locations or profile data were collected (names were dropped in the Flipkart
parser; Amazon bodies were taken without the profile element).

robots.txt (VERIFIED, fetched): Amazon.in and Amazon.ae do not disallow `/dp/` product pages and do not list
`/product-reviews`; Flipkart disallows `/reviews/` (individual review permalinks), not `/product-reviews/`.

### Setup and politeness

- Playwright in a throwaway venv in the session scratchpad. `playwright install chromium` failed twice
  (download from cdn.playwright.dev timed out at 30 s), so I used the already-installed Microsoft Edge
  (Chromium engine) via `channel="msedge"`, headless. Not the bundled Chromium; the engine is the same family.
- Delay at least 2.5 s between loads, no login, no cookies set by me, no captcha solving, no proxy. User agent
  was a normal Chrome string with a `review-iq-research` suffix.
- Page-load budget (60 per site) was respected but badly spent on Amazon.in: 60 of 60 used, 48 of them in two
  extraction runs whose review-body selector was wrong (empty text) and one run lost to a Windows encoding
  crash on an emoji while writing JSON. Result: Amazon.in yielded only 16 usable unique India reviews, not 50.
  Flipkart used 23 loads, Amazon.ae 18, Noon 1.

### Reachability

| Site | Result |
|------|--------|
| Flipkart | Search pages 200. Review pages 200 at `/<slug>/product-reviews/<itm>?pid=..&page=N`, 10 reviews per page, no login. The slug-less URL returns 404. Pages beyond about page 2 sometimes returned no reviews (e.g. 1,072 chars of text). No captcha, 403 or 503 seen |
| Amazon.in | `/product-reviews/<ASIN>` redirects to `/ap/signin` (login wall), so I did NOT use it. Product `/dp/<ASIN>` pages load 200 without login and carry about 8 to 10 "top reviews" each, which is what I sampled. No captcha, 403 or 503 seen in 60 loads |
| Amazon.ae | Search and `/dp/` pages 200, same structure as Amazon.in (earlier pass got 503 on `/product-reviews/` via WebFetch; I did not retry that path). 18 loads, no block |
| Noon (uae-en search) | Page loaded HTTP 200 but rendered "Something went wrong." three times and no products; I stopped after 1 load and did not try to get around it |

### Sampling and classification

Seed 42, `random.sample` over de-duplicated review texts, then I classified each text by reading it (single
rater, no second annotator, so no agreement figure). Raw text is in the scratchpad, not committed.

Classes (India): English, Hinglish (romanised Hindi mixed or alone), Devanagari Hindi, other, non-linguistic.
Classes (UAE): English, Arabic script, Arabizi, Hinglish, other.

| Sample | Pool (unique) | n | English | Hinglish | Devanagari | Arabic / Arabizi | Non-linguistic | Wilson 95% CI, Hinglish |
|--------|---------------|---|---------|----------|------------|------------------|----------------|-------------------------|
| Flipkart (5 products: kurta, polo t-shirt, earbuds, night cream, protein powder) | 94 | 50 | 50 | 0 | 0 | - | 0 | 0 to 7.1% |
| Amazon.in (2 products: mixer grinder, t-shirt), "Reviewed in India" only | 16 | 16 (all) | 16 | 0 | 0 | - | 0 | 0 to 19.4% |
| Amazon.ae (12 products over 6 queries), "Reviewed in UAE" only | 59 | 50 | 48 | 0 | - | Arabic script 2, Arabizi 0 | 0 | 0 to 7.1% |

Wilson 95% intervals (VERIFIED computed):
- Flipkart English 50/50: 92.9 to 100%. Amazon.in English 16/16: 80.6 to 100%.
- Amazon.ae English 48/50: 86.5 to 98.9%. Arabic script 2/50: 1.1 to 13.5%. Arabizi 0/50: 0 to 7.1%.
- Whole-pool heuristic checks: Flipkart 94 unique reviews, zero with Devanagari and zero with any of 40 common
  romanised-Hindi words (Wilson upper bound 3.9%). Amazon.ae pool 59 UAE reviews: 4 with Arabic script
  (6.8%, CI 2.7 to 16.2%), 0 Arabizi-pattern hits. The Hinglish marker list is mine and loose; it undercounts
  anything spelled unusually.

What the English cases look like: many are non-native or SMS-style ("God product", "Very chokky taste nd worst
taste ever", "Best Quility", "Vfm"). That is a spelling-noise problem, not Hinglish. I counted these as English.

### Comparison with the App Store substitute (uae-arabizi-sample.md, n=50)

| Metric | App Store (apps, UAE storefront) | Amazon.ae product pages (this pass) |
|--------|----------------------------------|-------------------------------------|
| English | 41/50 (82%) | 48/50 (96%) |
| Arabic script, incl. mixed | 7/50 (14%) | 2/50 (4%) |
| Arabizi | 0/50 | 0/50 |
| Hinglish | 0/50 | 0/50 |

The two sources agree that Arabizi and Hinglish are near zero in UAE e-commerce text. They differ on Arabic
script (14% vs 4%; the CIs 7.0 to 26.2% and 1.1 to 13.5% overlap but do not nest well). The App Store sample is
about app support complaints; this one is product reviews, so the product-review share of Arabic is plausibly
lower, but I only have one sample of each.

For India there is no App Store sample to compare to; the substitute I can compare to is the Reddit
posts (above), where Hinglish is written by the founders, not necessarily by reviewers.

### Biases that make the English count too high (BELIEVED, directions from how the pages work)

1. Flipkart's default sort is "Most Helpful", and Amazon's `/dp` page shows "Top reviews". Both favour
   longer, English-language, well-formed reviews. Hinglish may be more common among recent, short reviews.
   I did not test "Latest" sort.
2. Categories and products (bestsellers by search rank) skew to branded, mainstream listings. Hinglish may
   be more common in low-ticket categories, small-seller listings or tier 2/3 buyers.
3. Amazon.in sample is 16, not 50, because of my extraction bugs and the load cap. The upper bound on
   Hinglish there is 19.4%, so it contributes little.
4. Single rater; classification of a one-word review as English is a judgement.
5. Flipkart reviews are shown with city and name, and Amazon reviews with names; both were dropped.

### Net for the product decision

- Prevalence of Hinglish on Flipkart (0/50, upper bound 7.1%; pool upper bound 3.9%) is below what a
  Hinglish-first pitch would assume. BELIEVED: the real prevalence in the whole corpus is higher than 0 and
  probably single-digit percent, but this pass cannot support a "30% of reviews are Hinglish" claim.
  BELIEVED, from session memory notes not re-checked here: the earlier internal Flipkart corpus count
  (595 isolated vernacular of 245K deduped) points the same way, well under 1%.
- The better-supported differentiators from this pass are fake/hostile-review detection with early alerts, and
  separating product feedback from delivery noise on quick-commerce, not language handling.

## Reproduce

Reddit: stdlib `urllib`, `GET https://www.reddit.com/r/<sub>/search.rss?q=<q>&restrict_sr=1&sort=new&limit=100`
at 6 s spacing with a 15 s sleep on 429. Browser sampling: Playwright (Python) with `channel="msedge"`;
Flipkart `/<slug>/product-reviews/<itm>?pid=<pid>&page=N`, Amazon `/dp/<ASIN>` reading `[data-hook=review]`
and `[data-hook=reviewRichContentContainer]`. Seed 42 over de-duplicated texts. The live sites change;
re-runs return different reviews. Raw text is not committed.
