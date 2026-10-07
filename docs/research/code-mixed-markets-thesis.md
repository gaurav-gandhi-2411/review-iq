# Thesis: code-mixed markets the incumbents can't read

Date: 2026-10-07. Method: public web only (WebSearch/WebFetch, unauthenticated Apple iTunes RSS), no outreach.
Labels: **VERIFIED** = I fetched/ran it and saw it; **SNIPPET** = seen only in a search summary; **BELIEVED** = my inference.
Sources are in the table at the end (S-numbers). R-numbers are in-repo docs that live on unmerged branches (PRs #283, #284).

## Verdict up front

The headline "code-mixed markets the incumbents cannot read" is **not yet supported by evidence in any market**. What the
evidence supports is narrower: code-mixing is real and measurable in the Philippines, probably Malaysia and Indonesia, and
barely visible in the Gulf and East Africa; and "nobody handles it" is false for India. No market has a measured
incumbent failure, because **no vendor product was tested** (BELIEVED gap, not a finding).

## Evidence per market

| Market | Scale | Code-mixing in public text | Incumbent coverage | Verdict |
|---|---|---|---|---|
| **India** (Hinglish, Tanglish, Manglish) | Flipkart/Amazon corpus already in-repo | Academic: Roman-script Hindi in Indian reviews breaks English-only tools (S7, SNIPPET). In-repo benchmark work exists (R5). | Not empty: Sprinklr "Hindi Romanized" (R3, SNIPPET), Awshar AI Hinglish/Tanglish/Manglish claim (R3, VERIFIED listing), Mihup voice (R3). No vendor publishes a code-mixed accuracy figure (R3). | **Supported as a niche, not as a white space.** Zero first-person Indian seller quotes about the pain; Reddit unreadable (R1). |
| **Gulf** (Arabizi) | n/a | App-review sample n=50 (+1,000 heuristic): 0 Arabizi, 0 Hinglish; Arabic+Latin script mixing 2-4% (R4, VERIFIED, one rater, app reviews not product reviews). | Not examined. | **Not supported for the romanised wedge.** Arabic-script dialect is a different NLP problem (R4). |
| **Philippines** (Taglish) | SEA platform GMV $157.6B in 2025, Indonesia 37%, Philippines now #3; Shopee, Lazada, TikTok Shop+Tokopedia >98.8% (S1, SNIPPET; article paywalled, headline VERIFIED). Shopee PH app 810k store ratings, Lazada 1.19M (S2, VERIFIED). | My sample, Shopee PH app reviews: **6/30 = 20%** Tagalog-English mixed (Wilson 95% CI about 9-37%). Literature: FiReCS corpus >10k Taglish reviews; code-switching cut F1 by 35% (VADER), 8% (FSA), 9% (LLMs) (S8, SNIPPET). Taglish aspect extraction paper reports macro-F1 0.91 with an LLM (S9, VERIFIED abstract). | Brandwatch "104 languages", Talkwalker "187" (S10, SNIPPET); Taglish not named. Not tested. | **Best-supported market.** Real mixing, measured degradation of lexicon tools, but modern LLMs lose only ~9%: the defect is for cheap tools, not for LLM-based ones. |
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
| India | Run 50 labelled Hinglish reviews through Awshar's free trial and our pipeline; compare (R3 follow-up). Near $0. | Whether a differentiated accuracy exists against the one vendor that claims it. |
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
| S12 | Own sample: iTunes RSS `https://itunes.apple.com/{cc}/rss/customerreviews/page=N/id=<app>/sortby=mostrecent/json`, n=30 per market, seed 42, one rater | VERIFIED (my method, unreproducible exactly: feed rolls forward) |
