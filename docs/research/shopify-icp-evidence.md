# Shopify ICP evidence (N2c + N2d)

Date: 2026-10-08. Method: WebSearch/WebFetch for N2c; curl/urllib against public pages and the
public Judge.me storefront widget endpoint for N2d. No logins, no bot-protection circumvention.
Research sampling for prevalence, not ingestion. Single rater.

Label key: VERIFIED = I fetched/ran it myself and saw the result. SNIPPET = seen only in a
search-result summary or a fetch-tool summary (re-check wording before external use).
BELIEVED = my inference.

## Bottom line

1. Do Indian Shopify D2C customers write Hinglish enough for the moat to carry over? Not in this
   sample. Of 200 reviews, 6 were Hinglish (3.0%, Wilson 95% CI 1.4-6.4%), 180 English (90.0%),
   0 Devanagari, 1 other-regional (Latin-script, low confidence), 13 non-linguistic (emoji,
   punctuation, pasted product titles, one pasted address). Among reviews with actual language
   (n=187) Hinglish is 3.2% (CI 1.5-6.8%). VERIFIED (own sample, below).
2. This is a floor-biased estimate, not a market-wide one: 9 brands only, all on Judge.me, the
   skincare/personal-care/audio/mattress mix, first-page default ordering of a product's reviews.
   The mass-market-leaning brands in the sample (Beardo 3/37, Mamaearth 2/23) were the only ones
   with Hinglish at 8-9% (tiny n, CIs wide). BELIEVED: Hinglish share is higher for
   price-sensitive, Tier-2/3 buyers and on marketplaces than on D2C storefronts; this sample
   cannot test that.
3. Implication (BELIEVED, from the numbers): the Hinglish moat is thin as a headline for Shopify
   D2C; at ~3% it is an edge case the product handles, not the reason a D2C brand buys. The
   Shopify-ICP pitch needs to lean on something else (response drafting, defect/trend detection,
   English quality) and treat Hinglish as a robustness feature.
4. Connector risk (N2c): the Standard Product Review metaobject our connector reads is documented
   as "a restricted definition available to approved product review apps". Whether a non-review
   app such as ours can read it without joining the syndication program is NOT established by the
   docs I read and was not tested on a dev store. See N2c.

## N2c. Which review apps Shopify brands use, and how their data is reachable

Already known in repo (app/core/ingestion/shopify_source.py docstring): the native Shopify
Product Reviews app was shut down May 2024; reviews live in the Standard Product Review
Metaobject written by participating apps. Not repeated here beyond the new points.

### Market share evidence

| Evidence | Numbers | Label |
|---|---|---|
| Koala, tracked stores, data dated 2026-08-24 (koala-apps.io/blog/best-shopify-apps/product-review-apps/) | Judge.me 2,520; Shopify Product Reviews (discontinued) 804; Loox 768; Trustoo Ali Reviews Importer 203; AfterShip Product Reviews 179; Vitals 146; Yotpo 117; Stamped 92; Okendo 78 | VERIFIED fetch (Judge.me, native, Loox figures); the rest SNIPPET. Sample is Koala's tracked stores, not all of Shopify |
| BuiltWith install ranges (via search summary of a comparison page; BuiltWith not fetched) | Shopify Product Reviews 31K-50K; Judge.me 21K-30K; Yotpo Reviews 11K-20K; Loox 11K-20K; Stamped 11K-20K | SNIPPET |
| Shopify Plus adoption (same search summary, source page not identified) | Judge.me 26.44% (19,768); Yotpo 9.30%; Loox 6.71%; Okendo 6.13%; Stamped 4.28% | SNIPPET, weakest of the three |
| My own sweep of Indian D2C homepages (HTML string match, 2026-10-07) | 17 Shopify-marked domains contain `judge.me` strings (Mamaearth, boAt, Bombay Shaving, SUGAR, Plum, MCaffeine, Bear House, Perfora, Dot & Key, Sleepycat, Earth Rhythm, Beardo, Soulflower, Man Company, Traya, Zouk, Noise); Loox on 2 (Giva, Nobero); Yotpo on 2 (Minimalist, Vahdam) | VERIFIED strings present; presence of a string is not proof the widget is the live review system. The candidate list was my own, not a random draw |

Reading: Judge.me dominates SMB and Indian D2C by every source; Yotpo/Okendo skew to Plus and
enterprise; the discontinued native app is still present on many stores (those reviews are not
in any metaobject).

### Reachability per app

Admin API route = the Standard Product Review metaobject (`metaobjects(type:"product_review")`,
scopes `read_metaobjects` + `write_product_reviews`). Shopify docs (shopify.dev standard review
metaobject page, VERIFIED fetch): "restricted definition available to approved product review
apps"; no documented path for non-review apps; `reviews.rating` / `reviews.rating_count`
metafields are visible only via GraphQL Admin API. Shopify's changelog says any partner can build
a reviews app that syndicates, and apps in the program must syndicate all valid reviews
(VERIFIED fetch / SNIPPET). The changelog does not say whether a third-party app may read.

| App | Metaobject / Admin API | Own API / connector | Source and label |
|---|---|---|---|
| Judge.me | Syndication to Shop app exists (Judge.me blog title only); metaobject write not confirmed | REST `GET https://api.judge.me/api/v1/reviews` with `api_token` (private) + `shop_domain`, `per_page` max 100; webhooks need the Awesome plan. Token is created by the merchant in Judge.me admin | judge.me/help/en/articles/8409180 (SNIPPET). Public storefront widget JSON also returns reviews without auth (VERIFIED by me, N2d) but that is a render endpoint, not a sanctioned API |
| Loox | Shop app syndication stated; metafield `avg_rating`/`num_reviews` read via Storefront API returned null in one community report | API + webhooks claimed by vendor listing | community.shopify.com thread 408903 and third-party listing (SNIPPET) |
| Yotpo | Two-way syndication with Shop app (reviews copied to Shopify, counter in Shop > Reviews) | Reviews API `GET /v1/apps/{app_key}/reviews`, merchant + storefront endpoints | support.yotpo.com syndication page, apidocs.yotpo.com (SNIPPET) |
| Stamped | Two-way Shop app sync | REST, HTTP Basic with public + private key (developers.stamped.io) | stampedsupport.stamped.io, apitracker (SNIPPET) |
| Okendo | Program open to any partner; Okendo's own participation not found; one user report says retailer syndication was not possible | `/reviews` list endpoint (docs.okendo.io) | SNIPPET |
| Junip | Two-way Shop app syndication | API not confirmed | help.junip.co article 11421362 (SNIPPET) |
| Ali Reviews | Not found | CSV import/export only found; no API found | support.fireapps.io (SNIPPET); absence is weak evidence (101a) |
| Shopify native (dead) | None (discontinued 2024-05-06) | CSV export only | Koala (VERIFIED fetch); fireapps (SNIPPET) |

Implications (BELIEVED):
- A metaobject-only connector covers only merchants whose app is in the syndication program, and
  only if our app is allowed to read the restricted definition. Untested on a dev store: do that
  before building anything further on the connector.
- Per-app connectors via the merchant's own API token (Judge.me first, by share) are the
  documented path that does not depend on Shopify's restriction; Yotpo and Stamped have
  documented keys-based APIs as well.

## N2d. Hinglish fraction in Indian Shopify D2C reviews

### Method

1. Candidate Indian D2C domains (about 45 domains probed, my list, not random). Shopify confirmed by
   `Shopify.shop = "<x>.myshopify.com"` in homepage HTML. Domains with zero Shopify strings are
   reported as "no Shopify markers found", not as "not Shopify" (headless is possible).
2. For Judge.me brands: product handles taken from homepage links, product id from the public
   `/products/<handle>.json`, reviews from the public widget endpoint the storefront itself
   calls (`judge.me/reviews/reviews_for_widget?...&product_id=...&per_page=10&page=1..2`).
   User-Agent identified as research; 2-4 s between requests. Only review body text was kept; no
   reviewer names, emails, photos or dates.
3. Per brand: up to 2 products and 40 reviews, widget default order (not randomised).
   Pool: 334 fetched, 308 after exact-duplicate removal.
4. Sample: `random.seed(42)`, `random.sample(pool, 200)` (65% of the pool, so this is close to a
   census of what I could fetch, not a thin sample of a large frame).
5. Classification: single rater (me), by reading. Classes: English; Hinglish (Latin-script Hindi
   mixed or alone); Devanagari Hindi (checked by Unicode range U+0900-097F, so mechanical);
   other-regional; non-linguistic (emoji/punctuation only, pasted product titles, addresses).
   A second pass searched all 200 for common Hindi romanisation tokens to catch misses: no new
   hits. No second rater, so no agreement statistic; borderline short items ("Thik thik ha",
   "G faad", "Froud sale hai") were called Hinglish; "Sob bad product" (likely Bengali/Hindi
   "sab", ambiguous) was called other-regional. Moving that one changes nothing material.
6. Wilson 95% intervals.

### Results (n = 200)

| Class | Count | Share | Wilson 95% CI |
|---|---|---|---|
| English | 180 | 90.0% | 85.1-93.4% |
| Hinglish | 6 | 3.0% | 1.4-6.4% |
| Devanagari Hindi | 0 | 0.0% | 0.0-1.9% |
| Other-regional | 1 | 0.5% | 0.1-2.8% |
| Non-linguistic | 13 | 6.5% | 3.8-10.8% |

Excluding non-linguistic (n=187): Hinglish 6 = 3.2% (CI 1.5-6.8%).

### Per brand (sample counts)

| Brand | n | English | Hinglish | Other-regional | Non-linguistic |
|---|---|---|---|---|---|
| Beardo | 37 | 31 | 3 | 1 | 2 |
| Earth Rhythm | 38 | 37 | 0 | 0 | 1 |
| Mamaearth | 23 | 17 | 2 | 0 | 4 |
| Sleepycat | 24 | 21 | 0 | 0 | 3 |
| Dot & Key | 21 | 21 | 0 | 0 | 0 |
| Perfora | 21 | 20 | 1 | 0 | 0 |
| MCaffeine | 20 | 18 | 0 | 0 | 2 |
| boAt | 13 | 12 | 0 | 0 | 1 |
| The Bear House | 3 | 3 | 0 | 0 | 0 |

Per-brand CIs are not given: every brand has n <= 38 and at most 3 Hinglish items.

### Coverage: what was and was not sampled

- Sampled (9): the table above. The Bear House returned only 4 reviews in total.
- Shopify + Judge.me confirmed but BLOCKED by HTTP 429 (rate limiting) on all of 4 passes with
  back-off (one 60 s retry each, plus runs about 4-5 minutes apart): Bombay Shaving Co, SUGAR
  Cosmetics, Plum (one pass returned 0 reviews, others 429), Soulflower, The Man Company, Zouk.
  I stopped there rather than push harder. Which host returned 429 (brand site vs. product JSON)
  was not logged per call; treat as unknown.
- Shopify + Judge.me confirmed but 0 reviews returned: Traya, Noise (possibly widget uses
  non-default product ids; not investigated).
- Shopify with another review app, not sampled (no simple public endpoint tried): Giva, Nobero
  (Loox strings), Minimalist, Vahdam (Yotpo strings).
- Shopify markers present, review app not identified: Mokobara, Blue Tokai, Rare Rabbit,
  Foxtale, Snitch (few strings; weak).
- No Shopify markers found in homepage HTML: The Whole Truth, WOW Skin Science, Bewakoof, The
  Souled Store, Lenskart, Nykaa, derma co, Ustraa, Kapiva, HealthKart. Wakefit returned 403: stopped
  on that site. Pilgrim, The Sleep Company and others did not connect.
- Result: 9 brands with data, below the 15-25 target. All sampled data comes from one review
  vendor (Judge.me).

### Threats to validity (all BELIEVED effects, direction noted)

- Brand selection: my own list, skewed to well-known, English-forward urban brands. Downward bias
  on Hinglish.
- Category: skincare/oral care/audio/mattress. Categories with more Tier-2/3 mass buyers (value
  fashion, trimmers, kitchen) are mostly absent. Downward.
- Widget order and incentives: default order, and some brands' reviews look templated or
  incentivised (Dot & Key and Earth Rhythm are almost entirely long, polished English; I did not
  test this). Downward.
- Collection channel: reviews requested by email/post-purchase flows in English tend to get
  English replies. Downward relative to marketplaces.
- Single rater, small counts: the CI on Hinglish (1.4-6.4%) covers the plausible range for this
  frame but says nothing about frames I did not sample.
- Code-mixed text written in plain English words would not be counted as Hinglish. Not measurable here.

### 50 snippets (all classes represented; sample ids; no names or personal data)

The one non-linguistic item containing a pasted address and phone number (id 36) is counted in
the table but deliberately not shown.

| id | Brand | Class | Snippet |
|---|---|---|---|
| 0 | Beardo | English | Poor quality |
| 3 | Earth Rhythm | English | This is one of the best products I have ever had. |
| 6 | Beardo | English | Great |
| 24 | Beardo | English | Poor |
| 27 | Mamaearth | English | I but 2 pc but i received only 2 where is my buy 1 get 1 free product |
| 28 | Sleepycat | English | Excellent quality |
| 31 | Mamaearth | non-linguistic | Tea Tree Pimple Control Face Wash with Tea Tree & Salicylic for Oily & Acne-P... |
| 34 | Mamaearth | English | Great face wash recommend |
| 35 | Beardo | Hinglish | Waste of money don't buy it 1 hr bhi nhi chal ta h not long lasting perfume t... |
| 37 | Mamaearth | non-linguistic | . |
| 40 | Perfora | English | dentist impressed |
| 42 | Mamaearth | English | Good product |
| 44 | MCaffeine | English | Good 😊👍 |
| 52 | boAt | English | Yes |
| 55 | Earth Rhythm | English | Good |
| 56 | boAt | English | So good 😊💯 |
| 57 | boAt | English | very nice airbus so good company all so happy castmar |
| 59 | Perfora | English | It changed my oral care routine. Visible results. Superb serum |
| 65 | Mamaearth | English | MY FIRST TIME EXPERIENCE IT'S A GOOD FACE WASH 🙂 |
| 79 | Earth Rhythm | English | Great |
| 85 | Beardo | non-linguistic | 👎👎👎👎👎 |
| 86 | Mamaearth | non-linguistic | 👍🏻 |
| 90 | Mamaearth | English | Good 😊👍🏻 |
| 93 | Perfora | Hinglish | Bahut acha product hai teeth ke peele pan hatakar teeth white banata hai |
| 95 | Mamaearth | Hinglish | Froud sale hai |
| 100 | Beardo | Hinglish | Thik thik ha |
| 102 | Sleepycat | non-linguistic | Ultima Memory Foam Mattress |
| 104 | Mamaearth | Hinglish | Froud sale hai b1g1 bolte hai bs aata kuchh ni hai order mat krna kuchh bhi |
| 108 | MCaffeine | English | Fantastic |
| 112 | Sleepycat | English | Appropriate quality |
| 115 | Earth Rhythm | English | This cream capsule is beautiful and effective. |
| 122 | Earth Rhythm | English | Average results , nothing like what it claims. |
| 125 | Earth Rhythm | English | Smells good - drys quickly - but not a good moisturiser.. |
| 130 | Beardo | Hinglish | G faad |
| 141 | Sleepycat | English | Good product |
| 145 | Beardo | English | Amazing experience |
| 149 | Mamaearth | English | All good, good product & result |
| 154 | Beardo | English | Very nice |
| 170 | boAt | English | Love you boat Good product Long life bettery Good bass |
| 171 | Beardo | other-regional | Sob bad product , it raises prblm |
| 176 | Sleepycat | English | Nice |
| 179 | Beardo | English | Great product |
| 180 | Earth Rhythm | English | Good quality product. |
| 182 | Sleepycat | English | Very good product. |
| 183 | MCaffeine | English | Totally recommended just buy it |
| 186 | Perfora | English | strong teeth now |
| 191 | MCaffeine | English | Good product with good quality |
| 192 | Beardo | non-linguistic | 👌👌👌👌👌 |
| 194 | MCaffeine | English | best product best quality |
| 199 | Earth Rhythm | English | It's a good face mask. |

## Reproduction

Scripts were run from a scratchpad and are not committed; the sample texts are not committed
beyond the 50 snippets above. Endpoint pattern and seed are given in Method so the draw can be
redone, but widget content changes over time, so counts will drift.
