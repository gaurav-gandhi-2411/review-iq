# Thesis: code-mixed markets the incumbents can't read (Hinglish claim retired 2026-10-08, pending N1b)

Date: 2026-10-07; ICP section, N3b and N3c added 2026-10-08. Method: public web only (WebSearch/WebFetch, unauthenticated Apple iTunes RSS), no outreach.
Labels: **VERIFIED** = I fetched/ran it and saw it; **SNIPPET** = seen only in a search summary; **BELIEVED** = my inference.
Sources are in the table at the end (S-numbers). R-numbers are in-repo docs that live on unmerged branches (PRs #283, #284).

## Position as of 2026-10-08 (Q2): wedge, ICP, and what is retired

- **Wedge: triage and honesty.** Find the review that needs a human, separate product feedback from delivery noise, and publish
  measured accuracy with error bars while abstaining ("unclear") instead of guessing. This is what the first-person seller
  quotes in `docs/research/n5-research-gaps.md` (R7) describe: detection lag on a fake-1-star thread (a commenter), a
  Hinglish-writing D2C snack owner asking how to filter product feedback out of late-delivery and rider noise, and a new D2C
  seller tired of reading reviews manually, with the top reply telling them to paste reviews into ChatGPT or Claude. The
  site quotes the first two verbatim and the third in part, each linked. That reply is the substitute to beat.
- **Hinglish claim: retired as a headline, pending N1b.** The live hero "Half your reviews aren't in English" (live since
  2026-09-12, commit 1813853) was contradicted by our own samples: Hinglish is 106 genuine candidates in 14,552 Flipkart
  reviews (0.74%), 0 of 50 Flipkart and 0 of 16 Amazon.in in the N5 samples, and 6 of 200 in Indian Shopify D2C (R6).
  Hinglish stays on the site as a capability line ("Handles English and Hinglish"), which is true and measured in the held-out
  results. "Off-the-shelf tools miss it" and "we beat plain models" remain **unsupported**: N1b has NOT been run, and the
  site makes neither claim.
- **ICP: Shopify D2C brands in India via Judge.me**, agencies second (see the ICP section below). The Shopify connector
  path is not yet reachable by users, so the site says "online brands", not "Shopify brands".
- **Honest negatives kept:** the moat does not carry to the Shopify ICP on current evidence; no Indian seller was found
  saying Hinglish reviews are unreadable by tools (R7, bounded search); the topic field is among the weaker measured
  fields, and the site now says so.

## Verdict up front

The headline "code-mixed markets the incumbents cannot read" is **not yet supported by evidence in any market**. What the
evidence supports is narrower: code-mixing is real and measurable in the Philippines, probably Malaysia and Indonesia, and
barely visible in the Gulf and East Africa; and "nobody handles it" is false for India. No market has a measured
incumbent failure, because **no vendor product was tested** (BELIEVED gap, not a finding).

## ICP update (2026-10-08): Shopify D2C brands in India first, agencies second

This supersedes the agency-first framing in the earlier drafts of this doc. Labels as above. ICP evidence comes from
`docs/research/shopify-icp-evidence.md` (PR #299, unmerged when written, R6), `docs/research/n5-research-gaps.md` (R7),
`docs/research/m3-feature-scoping.md` (R8) and `docs/runbooks/shopify-first-install.md` (R9).

### Why Shopify first: ingestion legality, not market size

| Channel | What a SaaS can legally pull | Label |
|---|---|---|
| Amazon | SP-API Customer Feedback returns aggregated topics and trends, not review text; India is not among the listed marketplaces (US, UK, FR, IT, DE, ES, JP) (R8) | VERIFIED (Amazon doc, fetched in R8) |
| Flipkart | Seller API documents Listing, Order, Report, Notification sections and no ratings/reviews endpoint (R8) | VERIFIED (fetched in R8) |
| Scraping either site | Believed prohibited by both sites' terms. The ToS wording was not read: Amazon's page returned HTTP 403 and Flipkart's URL returned a search shell (R7) | BELIEVED |
| Shopify | Merchant installs our app (OAuth) or issues a token; reviews sit in their review app. The only self-serve, sanctioned path found | BELIEVED, from the rows above plus R6/R9 |

Absence caveat (rule 101a): "no endpoint found" is bounded by the docs read. A partner programme or a seller CSV export
could still exist; CSV upload stays in the product as the fallback.

### What it changes

- Self-serve onboarding (install, backfill, done) replaces the sales-led agency motion as the primary path.
- Distribution through the Shopify App Store becomes a real channel and a real dependency (app review).
- Ingestion becomes per-review-app, because most Shopify brands do not keep reviews in Shopify itself (see below).
- Agencies stay as the second ICP: one agency multiplies installs, but the demand evidence for them is thin (R1, R2).

### What it costs

- Shopify app review is calendar time outside our control (R8: Partner app and review not started).
- Compliance webhooks are not built: no `app/uninstalled` handler (so `revoked_at` is never set automatically) and none of the
  GDPR topics (`customers/data_request`, `customers/redact`, `shop/redact`) (R8, VERIFIED from the repo at a08fff0). That they
  are mandatory for public apps is BELIEVED; confirm in the app-review docs. About 1 engineering day (R8 estimate).
- Revenue share: the premise "20% only above $1M" is not what shopify.dev says. The page (fetched 2026-10-08) lists the first
  USD 1,000,000 of annual app revenue at 0% and anything above at 15%, plus a 2.9% processing fee on earnings; the 0% tier does
  not apply at USD 20M+ app revenue or USD 100M+ company revenue. A search summary says the default for developers who
  have not registered for the reduced plan is 20% (SNIPPET, not on the fetched page). Either way it is irrelevant at our
  scale, provided we register for the reduced plan. Source: https://shopify.dev/docs/apps/launch/distribution/revenue-share
  (S14; read through a summarising fetch tool, so exact wording of the registration step was not seen).

### New evidence, stated plainly

| Question | Finding | Provenance |
|---|---|---|
| Hinglish share in Indian Shopify D2C reviews | 6 of 200 = 3.0% (Wilson 95% CI 1.4-6.4%), 180 English (90%), 0 Devanagari. 9 brands, all Judge.me public widget, widget default order, one rater | R6, own sample, unmerged PR #299 |
| Is that a floor? | Probably. The brand list is the author's own and skews English-forward and urban; categories skew skincare/audio/mattress; 6 more brands were rate-limited (HTTP 429) and not sampled. The two mass-market-leaning brands had 8-9% on n of 37 and 23 (wide CIs). A higher share elsewhere is BELIEVED, untested | R6 |
| Marketplaces, same question | Flipkart 0 of 50; Amazon.in 0 of 16 (underpowered); Amazon.ae 0 of 50, Arabizi 0 of 50. Sort order and category bias toward English | R7, own samples |
| Which review app | Judge.me dominates: 2,520 of Koala's tracked stores vs Loox 768, Yotpo 117, Okendo 78 (data dated 2026-08-24; the discontinued native app still shows 804) | R6, partly SNIPPET |
| Can our metaobject connector read reviews? | shopify.dev describes the Standard Product Review metaobject as "a restricted definition available to approved product review apps". Whether a non-review app can read it was NOT established and not tested on a dev store | R6, open question |

### Risk to the already-built Shopify connector (PRs #291-#293)

The connector, its backfill and the Connect page all read `metaobjects(type:"product_review")` with `read_metaobjects`
(R8, R9). If Shopify's restriction blocks non-review apps, that path returns nothing for us, and even where it works it
covers only merchants whose app syndicates into the metaobject. Per-app connectors using a merchant-issued API token are
the documented alternative: Judge.me first (REST `api/v1/reviews`, token created by the merchant in Judge.me admin; SNIPPET
in R6), then Yotpo and Stamped. **Recommendation (BELIEVED): do not build further on the metaobject connector until a
dev-store test settles the read question; that test is blocked on GG (R9).** PRs #291-#293 are not wrong, but they may be
reading an empty source.

### What this does to the moat claim

On current evidence **the Hinglish moat does not carry to the Shopify ICP**: about 3% of reviews, itself a floor, is an edge
case a product handles, not the reason a brand buys. The positioning the evidence does support:

1. Measurement and abstention discipline (published interval, unscored fields stated, coverage reported).
2. Fake/hostile-review detection and separating delivery noise from product feedback (the strongest first-person pain in R7,
   including the one Hinglish-writing D2C founder).
3. Code-mixed robustness as a feature: **unproven** against plain models.

The deciding moat test **N1b has NOT been run**: plain frontier models vs Samidha on the 70 unseen held-out reviews (harness
in PR #289, `eval/experiments/moat_test_baseline.py`, dry-run only). It is blocked on a permission decision (review text
leaving the machine to a third-party model API, plus spend). Until it runs, any claim that we beat plain models is
unsupported; the published held-out result measures our pipeline alone.

## Evidence per market

| Market | Scale | Code-mixing in public text | Incumbent coverage | Verdict |
|---|---|---|---|---|
| **India** (Hinglish, Tanglish, Manglish) | Flipkart/Amazon corpus already in-repo | Academic: Roman-script Hindi in Indian reviews breaks English-only tools (S7, SNIPPET). In-repo benchmark work exists (R5). | Not empty: Sprinklr "Hindi Romanized" (R3, SNIPPET), Awshar AI Hinglish/Tanglish/Manglish claim (R3, VERIFIED listing), Mihup voice (R3). No vendor publishes a code-mixed correctness figure (R3). | **Supported as a niche, not as a white space.** Zero first-person Indian seller quotes about the pain; Reddit unreadable (R1). |
| **Gulf** (Arabizi) | n/a | App-review sample n=50 (+1,000 heuristic): 0 Arabizi, 0 Hinglish; Arabic+Latin script mixing 2-4% (R4, VERIFIED, one rater, app reviews not product reviews). | Not examined. | **Not supported for the romanised wedge.** Arabic-script dialect is a different NLP problem (R4). |
| **Philippines** (Taglish) | SEA platform GMV $157.6B in 2025, Indonesia 37%, Philippines now #3; Shopee, Lazada, TikTok Shop+Tokopedia >98.8% (S1, SNIPPET; article paywalled, headline VERIFIED). Shopee PH app 810k store ratings, Lazada 1.19M (S2, VERIFIED). | My sample, Shopee PH app reviews: **6/30 = 20%** Tagalog-English mixed (Wilson 95% CI about 9-37%). Literature: FiReCS corpus >10k Taglish reviews; code-switching cut F-score by 35% (VADER), 8% (FSA), 9% (LLMs) (S8, SNIPPET). Taglish aspect extraction paper reports macro F-score 0.91 with an LLM (S9, VERIFIED abstract). | Brandwatch "104 languages", Talkwalker "187" (S10, SNIPPET); Taglish not named. Not tested. | **Best-supported market.** Real mixing, measured degradation of lexicon tools, but modern LLMs lose only ~9%: the defect is for cheap tools, not for LLM-based ones. |
| **Malaysia / Singapore** (Manglish, Singlish) | Shopee MY 982k, SG 134k store ratings (S2, VERIFIED) | MY sample **4/30 = 13%** Malay-English or Chinese-English mixed (CI about 5-30%), plus 4 monolingual Malay. SG **0/30** (upper bound about 11%): Singlish particles absent; Vietnamese and Chinese reviews appeared instead. | Not examined. No Manglish/Singlish e-commerce paper found (S6 search). | **Malaysia: plausible, weak. Singapore: not supported.** |
| **Indonesia** (Bahasa-English) | Largest SEA market, 37% of GMV (S1). Shopee ID 2.18M, Tokopedia 652k store ratings (S2). | Sample **5/30 = 17%** with English words inside Bahasa (CI about 7-34%), but most of it is borrowed tech vocabulary ("crash", "bug", "download") that Bahasa models already see. 2 English-only. Many academic Shopee-ID sentiment papers exist (S11, SNIPPET), so Bahasa itself is a crowded research area. | Not examined. | **Weak.** Mixing is mild and mostly loanwords; Bahasa-only handling is the real task. |
| **Nigeria** (Pidgin) | Jumia NG app 153k store ratings (S2, VERIFIED). Jumia Q4 2025 NG physical GMV +50%, orders +33% (S3, SNIPPET). | Sample **1/30** clear Pidgin; scan of all 450 reviews found about 5 Pidgin-bearing ones (about 1%). NaijaSenti: Pidgin is one of 4 Nigerian languages with ~30k tweets each, a significant fraction code-mixed (S4, SNIPPET) - on Twitter, not reviews. | Not examined. | **Not supported for reviews.** Real on social, rare in the app reviews I could reach. |
| **Kenya** (Sheng, Swahili-English) | Jumia KE 34k store ratings, Kilimall 407 (S2, VERIFIED). Jumia KE orders +50% Q4 2025 (S3, SNIPPET). | Sample **0/30** (upper about 11%); marker scan 0/268 (heuristic, upper about 1.4%). RideKE: 29k code-switched Kenyan tweets, ride-hailing domain (S5, VERIFIED abstract). | Not examined. | **Not supported for reviews.** |

### Sampling method (applies to every row from my own pull)

Source: Apple iTunes RSS `customerreviews` for the shopping **apps** (not product reviews), up to 500 most recent per app
(PH 500, SG 500, MY 500, ID 500, NG 450, KE 268). 30 drawn per market with `random.seed(42)`, read and classified by
one rater (me), no second annotator, so no agreement figure. Classification is judgement, especially for Indonesian
loanwords and Malay. Product review pages (Shopee, Lazada, Tokopedia, Jumia, Kilimall) are JS-rendered or gated and
were **not** sampled. My automated marker heuristic found PH 18/500 and MY 0/500 versus 20% and 13% by hand, so it
undercounts and is only used as a floor. App reviews skew to complaints about the app and are shorter than product
reviews. Raw reviews are third-party text and are not committed.

## What the evidence supports, and does not

- **Supports:** Philippines (Taglish) as the clearest code-mixing market; India as a niche where mixing is real but
  contested by incumbents (R3); Malaysia as a maybe.
- **Does not support:** Gulf Arabizi (R4), Singapore Singlish, Nigeria and Kenya on review text, Indonesia as a mixing
  problem.
- **Not tested anywhere:** whether any incumbent actually reads these markets badly. Capability claims and my
  absence-of-mention findings are not accuracy evidence (R3).

## Cheapest deciding experiment per market

| Market | Experiment (cost) | Decides |
|---|---|---|
| India | Run 50 labelled Hinglish reviews through Awshar's free trial and our pipeline; compare (R3 follow-up). Near $0. | Whether a differentiated correctness exists against the one vendor that claims it. |
| Gulf | Prospect-supplied Noon/Amazon.ae product-review export, count Arabizi share (R4). $0. | Whether the wedge exists in product text at all. |
| Philippines | Take 200 Taglish product reviews (from a seller's own export, or FiReCS if licence permits), score our model vs VADER vs a general LLM on aspect extraction. Low LLM cost. | Whether we beat a plain LLM; S8 suggests the LLM gap is only ~9%. |
| Malaysia/Singapore | Draw 100 product reviews per country from a seller export; measure mixed share. $0. | Whether MY is 13% or noise; confirms SG is empty. |
| Indonesia | Hand-label 100 product reviews for English-in-Bahasa beyond loanwords. $0. | Whether mixing exceeds loanword noise. |
| Nigeria | Pull 200 public Jumia NG product reviews by browser, count Pidgin. $0. | Whether the 1% app-review figure holds for products. |
| Kenya | Same, 200 Jumia KE product reviews, count Sheng/Swahili. $0. | Same. |

## What would make this thesis false

1. Any market where a real seller's product-review export shows code-mixed share below roughly 5%: the market is
   English-only for our purposes (already the case for my UAE, SG, NG, KE samples).
2. A blind test where a general-purpose LLM, with no Samidha tuning, matches us on code-mixed aspect extraction within
   noise on at least 100 labelled reviews per market. Then there is no product, only a prompt (S8's ~9% LLM degradation
   already hints at a small gap).
3. A paying prospect who reads the mixed text themselves and says it is not their problem; the demand-evidence pass
   found zero first-person Indian quotes and could not read Reddit (R1). One real conversation outweighs this document.
4. An incumbent trial (Awshar, Sprinklr, Locobuzz) showing comparable accuracy at SMB price (R3).
5. Evidence that the buying agencies purchase solicitation and star-rating monitoring, not text analysis, which is what
   their public pages describe (R1, R2).

## What the public site should say under each outcome (N3b)

Written before the Q2 site change (2026-10-08, which retired the Hinglish headline and moved it to a capability line; the table below records what was changed and why). Two unknowns decide the hero: the moat test N1b (not run) and N2d (done: Hinglish about
3% in Shopify D2C reviews, floor-biased).

| Outcome of N1b on the 70 held-out reviews | Shopify brands mostly English (current N2d reading) | If Shopify brands prove Hinglish-heavy (needs a larger sample) |
|---|---|---|
| (i) A plain frontier model matches Samidha within the noise floor | Do not lead with Hinglish. Hero: "Read every review. Know which ones need you. Error bars shown." Positioning: measurement/abstention discipline plus workflow (reply drafting, alerts, delivery-vs-product split) | No reading claim at all; hero: "The review workflow for brands whose customers write Hinglish" |
| (ii) Samidha beats plain models by more than the noise floor | Do not lead with Hinglish; second line only: "Reviews, read honestly. Hinglish included, with the published numbers" (figures only via the metrics-injection system) | Lead with it: "Hinglish reviews, read accurately: measured against plain GPT, Claude and Gemini prompts, numbers published" |
| (iii) Mixed (wins some fields, ties or loses others) | Hero as in (i); name only the fields we win and link the full table | Same, with the claim limited to the winning fields |

Reading rule (BELIEVED): "beats" needs a paired comparison on the same 70 items against the existing noise floor, not two
point estimates. With 70 reviews and 10.7% of field-pairs unscored, only a large gap would clear it.

### Site claims that needed to change (grep of `site/index.html` for Hinglish/vernacular wording), and what Q2 did

| Line | Text | Problem (Q2 disposition in the last sentence) |
|---|---|---|
| 9 | `...sentiment and urgency, in English and Hinglish. Stateless by default...` (meta description) | Fine under (ii), reword under (i). Q2: reworded to a capability line ("Handles English and Hinglish") |
| 17, 23 | og/twitter description: `...in English and Hinglish.` | Same |
| 392 | `Built for Indian e-commerce · English & Hinglish · Early access` | Hinglish in the kicker; "Indian e-commerce" implies marketplaces we cannot legally ingest. Q2: now "Review triage for online brands" |
| 395 | `You manage reviews across a dozen brand storefronts, in a mix of English and Hinglish.` | Agency-first framing and an unmeasured mix; N2d says about 3% for Shopify D2C. Q2: Hinglish and the "mix" claim removed from the lede |
| 463-467 | `Half your reviews aren't in English` / `Real Indian marketplace reviews mix English and romanized Hindi in the same sentence — Hinglish. Off-the-shelf sentiment tools built for English miss the actual complaint buried in the Hinglish half.` | Contradicted by the samples: 0 of 50 Flipkart, 0 of 16 Amazon.in, 6 of 200 Shopify. "Half" has no evidence. "Off-the-shelf tools miss it" is what N1b would test and has not. Highest priority under every outcome. Q2: block removed, replaced by three seller pains with linked quotes |
| 536-537 | `...or connect Shopify/Google directly. English or Hinglish, no pre-translation needed.` | Shopify connect is not yet reachable by users (R8, R9). Q2: kept as "Handles English and Hinglish" (capability); the Shopify/Google connect wording is unchanged and still a separate gap |
| 908 | footer: `MIT License · evaluation-driven · built for Indian e-commerce` | Same as 392. Q2: now "review triage for online brands" |

Line 1035 (demo template printing counts of English and Hinglish mentions) is data-driven, not a claim; leave it.

## The Philippines (Taglish) deciding test, concretely (N3c)

Goal: does the pipeline beat a plain LLM and a lexicon tool on Taglish product reviews? S8 suggests the LLM gap is small.

### Data

- FiReCS (Filipino-English code-switched reviews): Hugging Face `ccosme/FiReCS`, **CC-BY-4.0**, no gating, 10,487 reviews
  (7,340 train / 3,147 test), taken from Google Maps and Shopee Philippines. VERIFIED (dataset card and file listing fetched
  2026-10-08, S13). Attribution is required; the licence permits our use. Labels are **sentiment only (3 classes), with no
  aspect annotations**, so FiReCS can test sentiment, not aspect extraction.
- Aspect extraction needs aspect labels: about 200 Taglish product reviews from a seller export, labelled by two raters (or
  by the three-judge panel, which is LLM-consensus silver and carries that caveat). No such export exists today.

### Exact comparison

1. Sentiment, FiReCS test split (n=3,147), three systems on the same items: VADER, a plain-prompt LLM (the PR #289 plain
   prompt adapted to Taglish), and our pipeline. Report macro F-score and share correct with bootstrap intervals, against
   the 34.5% majority-class rate.
2. Aspect extraction, 200 seller-export reviews: our `topics`/`pros`/`cons` vs the same plain LLM, through the existing
   `score_fixture` path (field-level, consensus-only). VADER produces no aspects, so it is excluded.
3. Decision rule fixed before running: if the plain LLM is within the interval of ours on both, the Taglish wedge is a
   prompt, not a product (falsifier 2 above).

### Cost (arithmetic on the PR #289 price table dated 2026-10-08; my estimate, not measured)

Assumes about 350 input tokens and 400 output tokens (1,500 for reasoning models) per review, 200 reviews.

| Model | Price per million tokens in / out (USD) | 200 reviews |
|---|---|---|
| anthropic/claude-haiku-5.5 | 0.10 / 0.50 | about $0.05 |
| openai/gpt-5-mini (reasoning) | 0.25 / 2.00 | about $0.62 |
| openai/gpt-5.5 (reasoning) | 5.00 / 30.00 | about $9.35 |
| our pipeline (production tier) | free-tier path | $0 marginal, but needs quota |

Running all 3,147 FiReCS items costs about 15.7 times those figures. The harness cannot be pointed at Taglish today: it is
tied to the 70 held-out Hinglish fixtures and the Hinglish prompt, so a loader and a prompt are needed (small).

### Can it run today with no spend and no seller export?

- Aspect extraction: **No.** No labelled Taglish aspect data exists, and it needs LLM calls.
- LLM sentiment arms (plain and ours): **No.** They need live LLM calls (spend or free-tier quota) and send third-party review
  text to a model API, which is the open permission decision behind N1b.
- **VADER on FiReCS: yes, and it was run.** $0, CC-BY-4.0, local, no review text left the machine. Throwaway venv containing
  only `vaderSentiment`; test CSV downloaded from Hugging Face; compound >= 0.05 positive, <= -0.05 negative, else neutral
  (the standard VADER cutoffs).

| System | n | Share correct | Macro F-score | Per-class F-score (neg / neu / pos) |
|---|---|---|---|---|
| VADER (English lexicon) | 3,147 | 47.1% | 0.450 | 0.535 / 0.253 / 0.560 |
| Majority class (neutral) | 3,147 | 34.5% | n/a | n/a |

Provenance: run 2026-10-08 by an uncommitted ad-hoc script in a scratchpad venv; FiReCS `FiReCS_test_set.csv` (453,641 bytes);
repo commit fa0292f. Deterministic single run, no interval. Meaning: an English lexicon tool sits 12.6 points above the
majority rate on Taglish and is weak on the neutral class, consistent with S8's finding that lexicon tools degrade. It is
**not** a result about our pipeline or about LLMs; it is the floor of the comparison and nothing more.

## Sources

| ID | Source | Label |
|---|---|---|
| R1 | `docs/research/demand-evidence.md` on branch `docs/s19-agency-prospects` (unmerged, PR #283) | VERIFIED (in-repo) |
| R2 | `docs/research/agency-prospects.md` on same branch | VERIFIED (in-repo) |
| R3 | `docs/research/competitor-scan.md` on branch `docs/s19-market-research` (unmerged, PR #284) | VERIFIED (in-repo) |
| R4 | `docs/research/uae-arabizi-sample.md` on same branch | VERIFIED (in-repo) |
| R5 | In-repo hi-en benchmark/eval docs (see repo memory index; not re-read for this doc) | BELIEVED pointer, not re-verified |
| S1 | https://www.dealstreetasia.com/stories/momentum-works-report-2025-479044 (Momentum Works, GMV $157.6B; details via search summary; also yicaiglobal.com mirror) | SNIPPET |
| S2 | https://itunes.apple.com/search?term=Shopee&country=ph&entity=software (and ph/sg/my/id/ng/ke variants; `userRatingCount`, fetched 2026-10-07; store-wide ratings, not review counts per product) | VERIFIED |
| S3 | https://www.insidermonkey.com/blog/jumia-technologies-ag-nysejmia-q4-2025-earnings-call-transcript-1693388/ (Jumia Q4 2025) | SNIPPET |
| S4 | https://aclanthology.org/2022.lrec-1.63 (NaijaSenti) | SNIPPET |
| S5 | https://arxiv.org/abs/2502.06180 (RideKE) | VERIFIED (abstract) |
| S6 | Web search "code-mixed Taglish Singlish Manglish ... e-commerce" 2026-10-07: surfaced Bengali-English, Tamil-English, Roman-Urdu sets, none for Taglish/Singlish/Manglish | SNIPPET (absence in one search; not proof of absence) |
| S7 | https://dl.ifip.org/hal-01448058v1 (English-Hindi review sentiment; via R1) | SNIPPET |
| S8 | https://archium.ateneo.edu/discs-faculty-pubs/411 and https://animorepository.dlsu.edu.ph/conf_shsrescon/2025/paper_csr/5 (FiReCS, F1 drops) | SNIPPET |
| S9 | https://arxiv.org/abs/2601.01827 (Taglish aspect extraction, macro-F1 0.91) | VERIFIED (abstract) |
| S10 | https://www.stork.ai/compare/brandwatch-vs-talkwalker (language counts; third-party) | SNIPPET |
| S11 | https://jurnal.umpp.ac.id/index.php/surya_informatika/article/view/2181 (one of several Indonesian Shopee sentiment papers) | SNIPPET |
| R6 | `docs/research/shopify-icp-evidence.md` on branch `docs/s19-shopify-icp-evidence` (PR #299, unmerged) | VERIFIED (in-repo) |
| R7 | `docs/research/n5-research-gaps.md` (main) | VERIFIED (in-repo) |
| R8 | `docs/research/m3-feature-scoping.md` (main) | VERIFIED (in-repo) |
| R9 | `docs/runbooks/shopify-first-install.md` (main) | VERIFIED (in-repo) |
| S13 | https://huggingface.co/datasets/ccosme/FiReCS (licence, size, labels) | VERIFIED (fetch-tool summary) |
| S14 | https://shopify.dev/docs/apps/launch/distribution/revenue-share | VERIFIED (fetch-tool summary) |
| S12 | Own sample: iTunes RSS `https://itunes.apple.com/{cc}/rss/customerreviews/page=N/id=<app>/sortby=mostrecent/json`, n=30 per market, seed 42, one rater | VERIFIED (my method, unreproducible exactly: feed rolls forward) |
