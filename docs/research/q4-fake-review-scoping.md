# Q4 - Fake-review detection: scoping its return

Date: 2026-10-08. Scope only: no build, no spend, no live API or LLM call. Method: public web (WebSearch/WebFetch) and `git`/file reads in this repo. Companion document: [q4-fake-review-eval-preregistration.md](q4-fake-review-eval-preregistration.md).

Evidence labels: VERIFIED = I fetched the page or read the file and the claim is in it. BELIEVED = from a search snippet, memory, or a page that did not render its license; confirm before relying on it.

## Bottom line

1. Fake-review detection is the strongest seller pain in `docs/research/demand-evidence.md` section A, but that evidence is Amazon UK/US/EU forum threads plus Indian press (not India seller quotes). The product removed the feature because it had no labels and no measurement (commits 3c6a4f4 and 47ee518, see Q4b). Nothing in the repo measures it today.
2. No public labelled dataset matches our deployment case (Indian e-commerce product reviews, written by humans paid or incentivised, scored zero-shot on a seller's own export). The two commercial-clean candidates are a 1,600-review hotel set (human-written fakes, wrong domain) and a 40,000-review Amazon set whose fakes are machine-generated (right domain, wrong fake type). Both are proxies. A result on them can falsify the idea but cannot by itself justify shipping it as a feature.
3. The removed engine is salvageable as a baseline to be measured, not as a product. Its heuristics and prompt are cheap to re-run; its input assumptions (star rating, batch dates) do not hold for what a seller export contains.
4. Recommendation: run the pre-registered eval (zero spend on free or local models), and treat a pass as authorising at most a flag-only hint until an in-domain labelled sample exists.

## Q4a - Public labelled fake-review datasets

License column quotes what I could actually read. Where a page did not render or carried no license text I say so; I did not infer a license. This follows the repo's method in ADR 0004 (license "resolved" only when read from the dataset's own page, never assumed).

| Dataset | Size | Label provenance | License as found | Commercial-clean? | Domain match | Source |
|---|---|---|---|---|---|---|
| Deceptive Opinion Spam Corpus v1.4 (Ott, Choi, Cardie, Hancock 2011) | 1,600 reviews of 20 Chicago hotels: 400 truthful positive (TripAdvisor), 400 deceptive positive (Mechanical Turk), 400 truthful negative (Expedia, Hotels.com, Orbitz, Priceline, TripAdvisor, Yelp), 400 deceptive negative (MTurk); 20 reviews per hotel per cell | Human-crowdsourced fakes (paid MTurk writers told to write a fake). "Truthful" side is scraped and assumed genuine, not verified | Search-result record for the Aston FoLD deposit states CC0. The record page itself rendered only a header, so I could not read the text | Likely yes (CC0) - BELIEVED, not VERIFIED. Confirm on the deposit page or the original download README before use | Poor: hotels, no star rating, 20 entities, US English, writers were paid to fake and are not a seller-attack population | [Aston FoLD record](https://fold.aston.ac.uk/handle/123456789/36) (BELIEVED); [paper](https://aclanthology.org/N13-1053/) for the negative extension |
| Li et al. 2014 multi-domain (hotel, restaurant, doctor; truthful, crowd deceptive, domain-expert deceptive) | Not verified | Crowd and domain-expert written fakes | Not found | Unknown | Better diversity, still not products | [ACL P14-1147](https://aclanthology.org/P14-1147/) (paper only; dataset page not located) - BELIEVED |
| Amazon Fake Review Labelled Dataset (Salminen et al. 2021/22) | 40,000: 20,000 computer-generated (GPT-2; ULMFiT was tried and rejected), 20,000 original; 10 Amazon categories (Books, Clothing, Electronics, Home and Kitchen, Kindle, Movies and TV, Pet, Sports, Toys, Video Games) | Fakes are model-generated, so label is exact by construction; "original" reviews are scraped Amazon reviews assumed genuine, and are known to contain real fakes | GitHub repo shows no license (VERIFIED: no license text in the repo content fetched). Hosted at OSF; the OSF page is JavaScript-rendered and its license field was not readable | Unknown. Treat as not cleared until the OSF license is read in a browser. The "original" half is Amazon content, so Amazon's terms may also apply | Best domain match (product reviews; star rating column believed present, not verified) but fake type is machine text, which is an easier and different problem than paid human fakes | [joolsa/FakeReviews](https://github.com/joolsa/FakeReviews) (VERIFIED size, generation, no license text); [OSF tyue9](https://osf.io/tyue9/) (license not readable) |
| Yelp Open Dataset | Millions of reviews | No fake labels. Only "recommended" reviews are published | Page states: "The Yelp Open Dataset is a subset of Yelp data that is intended for educational use." Full terms are in Yelp's linked ToS, which I did not read | NO - educational use stated (VERIFIED). Treat as restricted | Restaurants and local business | [Yelp open dataset](https://business.yelp.com/data/resources/open-dataset/) (VERIFIED quote) |
| YelpChi, YelpNYC, YelpZip (Rayana and Akoglu 2015; Mukherjee et al. 2013) | YelpChi 67,395 reviews, filtered share 13.23 pct; YelpNYC 359,052 reviews; YelpZip 608,598 reviews | Filter-inferred: the label is "Yelp's own spam filter put this in the not-recommended list". It is a proprietary-model output, not ground truth. Any detector trained on it learns to imitate Yelp's filter | Obtained by emailing the author per the ODDS page; no license text found. Derived from Yelp content, so Yelp's terms plausibly apply | NO / unknown (BELIEVED restricted). Do not use commercially | Restaurants. Has reviewer ids and dates, which we lack | [ODDS stonybrook](https://odds.cs.stonybrook.edu/?p=562) (page did not load for me; figures from search snippet - BELIEVED), [arXiv 2205.13422](https://arxiv.org/pdf/2205.13422) |
| Amazon Reviews 2018 (Ni, Li, McAuley) | 233.1 million reviews | No fake labels | The page has no explicit license or commercial-use statement (VERIFIED absent). I believe it is distributed for academic use; that is not stated on the page | Not cleared. Say "restricted until a license is shown", not "restricted by text I read" | Product reviews, but unlabelled for fakeness | [UCSD amazon_v2](https://cseweb.ucsd.edu/~jmcauley/datasets/amazon_v2/) |
| Amazon Reviews 2023 (McAuley Lab) | 571.54 million reviews, 33 categories; fields include rating, user id, timestamp, verified-purchase flag, helpful votes | No fake labels | No license text on the project page or the Hugging Face card content I fetched (VERIFIED absent) | Not cleared | Product reviews. Useful only as an unlabelled negative-pool or as a source of metadata for threat modelling | [amazon-reviews-2023.github.io](https://amazon-reviews-2023.github.io/), [HF card](https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023) |
| DeRev (Fornaciari and Poesio) | 236 reviews (118 deceptive, 118 truthful) kept from 6,819 collected | Journalistic investigation: reviewers who admitted being paid. Best real-world provenance of the set; tiny | Not found | Unknown | Amazon books | Search snippet only - BELIEVED |
| Kaggle "fake-reviews-dataset" (mexwell) | Page did not render | Appears to republish the Salminen set | Not readable | Unknown | As Salminen | [Kaggle](https://www.kaggle.com/datasets/mexwell/fake-reviews-dataset) (page content not retrieved) |
| Indian-market source | none found | - | - | - | - | Search returned thesis and press material about Flipkart/Amazon India fake reviews but no downloadable labelled corpus. Absence is only for the queries tried (one search), not a proof that none exists |

Repo-local data already covers India but not fakeness: the Flipkart Kaggle sources (CC0 per ADR 0004) carry no labels, no timestamps and no reviewer id (Q4b).

### Reading the table

- Label provenance decides what a score means. Human-crowdsourced fakes (Ott) measure "can you spot a paid MTurk fabrication"; filter-inferred (Yelp) measures "can you reproduce Yelp's filter"; generated (Salminen) measures "can you spot GPT-2 text". None measures "can you spot an incentivised or competitor-written review on an Indian marketplace", which is what a seller is asking.
- Only two sets have any plausible commercial path, and for both the license text was not read to completion. The pre-registration therefore makes license confirmation a precondition step, not an assumption.
- Research-only sets (Yelp family, probably the Amazon dumps) are allowed for internal measurement only if the repo decides internal research use is acceptable; they must never feed a shipped model or a published number without clearance. Recommended: do not use them.

## Q4b - What the removed scorer did

### Where it went

- 3c6a4f4 (PR #217, S15d D5): removed `POST /v2/authenticity`, `POST /v2/authenticity/batch`, `GET /v2/insights/authenticity`. Stated reason: the fake-review flag is unmeasurable.
- 47ee518 (PR #251, S17 W6): removed the `/bff/authenticity*` routes, the `include_authenticity` ingest option, the Authenticity and Flagged web pages, the per-review "Authenticity check" card, and the health-score authenticity term. That term was a constant 0.30 offset for every unaudited org, so it carried no information. Health score formula_version went 1.0 to 2.0. Recorded usage at removal: 33 audit rows, all internal orgs, none in 30 days, zero Cloud Run requests (from the commit message).
- Still in the tree (VERIFIED by listing): `app/core/authenticity/{engine,heuristics,batch_signals,schema}.py`, `app/core/prompts/authenticity.py`, `eval/authenticity/{runner,scoring,replay}.py`, 40 labelled fixtures in `eval/authenticity/fixtures/labeled.jsonl`, the `authenticity_audits` table (historical rows only). The engine is still imported by alerts and eval.
- `eval/results/authenticity_latest.json` is explicitly quarantined: its own status field says the perfect-score figures were reconstructed from a README claim, never produced by a replayable run, over 40 fixtures with 21 positives. Not a measurement.
- Product framing from the P1 insights memory: the API response was precision-first. It never used the words fake, genuine or suspicious; labels mapped to clear, flagged_for_review, priority_review. Dispositions were review priorities, not verdicts.

### Method (read from `git`-tracked files)

Per review, `score_single` combined three things:

1. LLM signal: Groq large model, language-aware prompt, JSON out with a 0 to 1 genuineness score and up to four flags (incentivised phrase, rating/text mismatch, generic low info, promotional tone). Falls back to a neutral 0.5 on any error.
2. Heuristic score: four hand-set penalties. Incentive phrases (English and Hinglish list, weight 1.0), brevity (under 8 words, weight 0.5), repeated-word ratio (weight 0.4), star/text sentiment mismatch using small positive and negative word lists (weight 0.8). Score is one minus the clamped penalty sum.
3. Blend: 0.4 times heuristic plus 0.6 times LLM; if the LLM score is below 0.65 the result is capped at the LLM score. Labels at 0.65 and 0.40 cut points. All weights and cut points are hand-set, none was fitted or calibrated against labels (the repo had none).

Batch signals added near-duplicate detection (3-word shingles, Jaccard, 0.60 cut) and review bursts from dates. The Phase 2 research detectors (`app/core/detectors/campaign.py`, plus `benchmark/phase2_synthetic/`) were built for coordinated-campaign detection and validated only on a synthetic testbed with planted patterns (seed 42, 837 records, real corpus text with fabricated timestamps and reviewer ids), labelled "SYNTHETIC-VALIDATED, NOT proven on real seller data" in the project memory.

### Evidence about accuracy: none that counts

- No real labels exist for the held-out set (README states it; runner states it).
- The 40 in-repo fixtures were written by the developer, 21 positives; the perfect score once published was quarantined as unreproducible. At that n a Wilson interval is wide enough to be uninformative.
- Phase 2 recon on the Flipkart corpus (project memory, local pandas, no LLM): no timestamps and no reviewer column in any of the three raw sources, so burst and cross-account coordination are impossible to test there; the fake-campaign signal was judged mostly artifact, because repeated text clusters traced to the same review cross-listed across collaborator uploads of the dataset, and short repeats ("nice product") were organic terseness spread over many products. Verdict recorded: batch-defect and trend NOT SUPPORTED, fake-campaign PARTIAL and weak on that corpus. (Source: auto-memory notes `project_phase2_recon.md` and `project_phase2_step1_signal.md`; I did not find a committed `docs/audit/` file, which the memory itself says never existed.)
- Heuristic caveat I can state from the code alone: three of the four heuristics (brevity, repetition, mismatch) flag ordinary terse or sarcastic genuine reviews, which are common on Indian marketplaces, so false flags on genuine reviews are the main risk, and nothing has measured that rate.

### Signals on a seller's export versus public datasets

| Signal | Seller export in review-iq today | Ott 2011/13 | Salminen | Yelp family |
|---|---|---|---|---|
| Review text | yes | yes | yes | yes |
| Star rating | not a CSV-ingest column (`csv_ingest.py` resolves text, product and date columns only; BELIEVED present when the Shopify connector supplies it) | no | yes | yes |
| Review date | optional column, auto-detected, ambiguous formats handled | no | not verified | yes |
| Reviewer id | no | no (no id field) | not verified | yes |
| Verified-purchase flag | no | no | not verified | no |
| Product id | optional column | hotel (20 entities) | category only | restaurant |

Consequences: reviewer-level and burst features that drive most published graph-based detectors (the Yelp-family literature) are not available to us; we are limited to text, optionally rating and date. So the realistic capability is text-plus-cross-review-similarity, which is exactly what the public text-only sets can measure. Domain shift risks, in order: (1) fake type (paid human, incentivised, competitor attack, generated) differs from every dataset's fake type; (2) language: all public sets are English, our target includes Hinglish and Hindi; (3) base rate: public sets are balanced, a seller export is mostly genuine, so precision at the real base rate is far lower than balanced-set precision and must be computed by re-weighting, not read off; (4) "genuine" labels in public sets are themselves unverified.

### Verdict: salvageable or rebuild?

Salvageable as a measured baseline and as plumbing (prompt, schema, eval runner with cassette replay, Wilson helper already in `eval/wilson.py`). Not salvageable as a shippable scorer: it has never been measured, its blend weights are arbitrary, and its star-rating dependence does not match the ingest path. Treat the removed engine as the "legacy" arm in the pre-registered ladder; whether it survives is decided by the result, not by its prior shipping history. A text-and-similarity classifier is the likely replacement if the LLM arm does not beat the cheap arms.

## Q4c - Pre-registration

See [q4-fake-review-eval-preregistration.md](q4-fake-review-eval-preregistration.md). It fixes datasets, split, metrics, ship thresholds, baselines, CI method, minimum n and falsification conditions before any run. No number about our own performance appears in either document because none exists.

## Open items for GG (not blocking this PR)

1. Read the Ott and Salminen license text in a browser (the two fetches that failed to render) and record the verbatim text in the pre-registration before the first run.
2. Decide whether a small in-domain labelled sample (consenting sellers, adjudicated by two reviewers) is worth building; the pre-registration states why shipping beyond a flag-only hint requires it.
3. The Indian-market dataset search was a single query; a second pass (Hugging Face, Zenodo, Kaggle Flipkart fake-review sets, Hindi review corpora) is cheap and should precede any in-domain sample plan.
