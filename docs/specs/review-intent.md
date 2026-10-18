# Review intent on the classification engine: spec (S21 Part 3, pre-registered before any labelling)

**Target user.** Sellers and D2C brands in beauty, apparel/lifestyle and food/wellness who sell on marketplaces
and their own stores (Judge.me, Shopify, Flipkart, Amazon), India first, English and Hinglish reviews.

**Pain point.** They cannot read every review. The ones that need action (a defect, a late or damaged
delivery, a refund request, a safety problem) are buried among praise and noise ("90% of them are about
'late delivery' or 'rider bhaiya'. Actual product feedback kaise filter karu?", a D2C founder's Reddit post quoted in
`docs/research/n5-research-gaps.md`), and by the time they notice, "the reviews have been up for days".

**Success metric.** PRECISION AT COVERAGE on a sealed test set, per task: the coverage at which precision of the
answered items reaches 0.95, and precision at coverage 0.9, 0.8 and 0.7, each with a 95% bootstrap CI;
accuracy and macro-F1 are reported next to it, never instead of it (E2 report, P0). A per-class floor applies:
no class below 0.60 recall at the operating point without being reported as a known weak class. The comparison
that decides whether the engine earns its place is the fine-tuned model against plain-prompted Llama 3.3 70B and
against the current pipeline, at MATCHED coverage.

**Who pays.** The seller or brand, per active store per month. Not decided here; the model's job is to make the
"needs action" queue small and trustworthy enough to pay for.

Status: DRAFT-PRE-REGISTERED. Nothing below has been run. Thresholds and the taxonomy are fixed by this file's
commit date; any change after the first label is made is a dated amendment at the bottom, never an edit.

---

## 1. What E2 taught us that this spec applies

| E2 finding | Applied here |
|---|---|
| Labelled volume moved the number; augmentation and active learning did not | The effort goes into the labelling panel and its agreement, not into augmentation. No back-translation, no active learning in the first model |
| In-language data matters (non-English 0.833 with in-language training against 0.639 without) | The corpus is stratified by language as it occurs, and each language the product claims must have in-language labelled rows before a claim is made for it |
| Hierarchy gave no gain | A flat taxonomy. The groupings in section 2 are for REPORTING only, never a training signal |
| e5-base is the default encoder; MiniLM where latency dominates | e5-base first; MiniLM as the latency option |
| Mahalanobis thresholds must be recalibrated on the int8 model | Any serving number is taken after `export()` recalibration (engine PR 338) |
| A model cannot be measured above its labels | The agreement ceiling (section 5) is the first gate, before any scale labelling |

## 2. Taxonomy

One review gets ONE primary intent (the thing a seller most needs to act on) and, optionally, secondary intents.
Multi-intent reviews are common ("it broke and I want a refund"), so agreement is measured on the primary label
and on each class's presence flag separately.

### T1. Intent (10 classes)

| Class | Definition (decision rule) | Seller pain it serves | Real example (source) |
|---|---|---|---|
| `product_defect` | The product itself is faulty, damaged by normal use, or not as described: broke, leaks, fell apart, wrong material, causes a reaction | "needs action": fix, replace, listing correction | "This top is absolutely gorgeous, but i followed the care tag to a t and it completely fell apart in the wash. will be returning." (apparel, CC0) |
| `delivery` | About shipping, courier, timing, arrival condition of the PARCEL, missing items, rider behaviour. Praise of fast shipping alone is `praise` | Separating logistics noise from product feedback (the "rider bhaiya" pain) | "As soon as the seller was contacted about part of the order missing they took care of it within two days." (food, CC0) |
| `customer_service` | About how the seller or support handled a contact: responsiveness, helpfulness, rudeness | Brand reputation, a reply is owed | "NuFace also has terrible customer service. Wish I hadn't bought the device at all." (beauty, CC BY 4.0) |
| `pricing` | About price, value for money, comparison of price | Pricing and promo decisions | "it's overpriced for a shirt. i'm sadly returning it." (apparel, CC0) |
| `praise` | Satisfaction with no actionable complaint and no question or request | Social proof, testimonial candidates, not an action queue | "Love it put it on at bed time and wake up w my lips feeling soft and hydrated." (beauty, CC BY 4.0) |
| `question` | The reviewer asks something the seller could answer ("does it come with...", "is this vegan?") | A reply is owed | Rare in post-purchase review corpora; see section 3 |
| `return_refund_request` | The reviewer asks for, or states they are doing, a return, refund or exchange | "needs action": it is escalating | "Made me break out, I was so disappointed. Returned immediately." (beauty, CC BY 4.0) |
| `suggestion` | A concrete request or wish for a change to the product, listing or service | Product roadmap | "I wish they were slightly cheaper and I also wish the brushes on the toy were available for sell seperately." (food, CC0) |
| `competitor_comparison` | Names or clearly refers to another brand or product to compare | Competitive intelligence | "have been drinking the Eight O Clock French Roast for years and it's now horrible and tastes like Folgers" (food, CC0) |
| `spam_irrelevant` | No product content: ads, links, off-topic, gibberish, a review of another product, a seller-service rating with no product content | Noise removal | To be collected: none found by keyword retrieval in the three English corpora |

Reporting groupings (never trained on): ACTION = defect, delivery, customer_service, return_refund_request,
question; INSIGHT = pricing, suggestion, competitor_comparison; SIGNAL = praise; NOISE = spam_irrelevant.

### T2. Sentiment: `positive | negative | neutral | mixed` (the existing pipeline's definition).
Judged on the TEXT only, with the star rating withheld, so the label does not just copy the stars.

### T3. Urgency: `low | medium | high` (the existing pipeline's rubric, unchanged)
HIGH = physical harm or safety risk (pain, injury, reaction), explicit escalation (refund or return demand, legal
threat), or a systemic defect (arrived broken, same failure repeating). MEDIUM = a concrete fixable defect with no
harm and no escalation. LOW = praise, neutral observation or subjective preference. Any harm signal is HIGH
even in a positive review. (Source: `app/core/prompts/en.py` v2.3.)

### T4. Buy again: `yes | no | unclear`. `no` only if the reviewer says they will not repurchase or would not
recommend; `yes` only if they say they will repurchase or recommend; otherwise `unclear`.

### T5. Rating-text mismatch: `yes | no`, judged with the star rating shown. `yes` when the stars and the text
point in opposite directions (5 stars with a complaint of a defect, 1 star with praise), the review-quality
signal sellers act on.

### T6. Aspect and aspect sentiment (category-specific, multi-label)
For each aspect the review mentions: present (yes/no) and, if present, `positive | negative | neutral`.

| Category | Aspects |
|---|---|
| Beauty | texture, fragrance, packaging, results_efficacy, skin_reaction, shade_match, value, longevity |
| Apparel/lifestyle | fit_size, fabric_quality, colour_accuracy, stitching_durability, comfort, style, value, length |
| Food/wellness | taste, freshness_expiry, packaging_seal, quantity, ingredient_quality, efficacy_claims, value, safety_allergen |

The aspect lists are drawn from the aspects the corpora's reviews actually discuss (fit, fabric and colour in
apparel; dryness, breakout and scent in beauty; taste, freshness and quantity in food) and are fixed for the
pilot. Marketplace non-category products (the Flipkart vernacular stratum below) are judged on T1 to T5 only.

## 3. What the real data show before we label (keyword retrieval, NOT prevalence)

Three English corpora were searched with simple patterns (a probe to check the definitions are usable, not a
count): `question` and `competitor_comparison` produce mostly false positives. Rhetorical questions ("what more
could you want?") dominate the `?` hits; "compared to the rest of the jumpsuit" is not a competitor. Real
questions and real brand comparisons appear to be rare in post-purchase review text (they live in marketplace Q and
A and in forum posts). Consequences, fixed now:

1. `question`, `competitor_comparison` and `spam_irrelevant` are expected to be RARE classes. They stay in the
   taxonomy because they matter to sellers, but the pilot reports their agreement separately and the model
   headline uses macro-F1 over classes that have enough support (>= 30 in the sealed test set); classes below that
   are reported as "insufficient support", not scored.
2. `suggestion` appears mostly as a secondary intent inside an otherwise positive review ("I wish it was less full
   around the waist"). The primary-intent rule resolves it to `praise` or `product_defect` when the main message
   is that, and the presence flag captures the suggestion.
3. Multi-intent is the rule, which is why primary and presence are scored separately.

## 4. Corpus for the pilot (licences read from the Kaggle API metadata, 2026-10-10)

| Stratum | Source | Licence (verbatim) | Language |
|---|---|---|---|
| Apparel | Women's E-Commerce Clothing Reviews (`nicapotato/womens-ecommerce-clothing-reviews`), about 23K | `CC0-1.0` | English |
| Food/wellness | Amazon Fine Food Reviews (`snap/amazon-fine-food-reviews`), about 568K | `CC0-1.0` | English |
| Beauty | Sephora Skincare Reviews (`melissamonfared/sephora-skincare-reviews`) | `Attribution 4.0 International (CC BY 4.0)`: attribution required | English |
| Vernacular (India) | Flipkart reviews already cleared in ADR 0004 (`data/processed/vernacular_subset.jsonl`, 393 rows) | ODbL-1.0 / DbCL-1.0 (internal research and derivatives; share-alike on redistribution) | Hinglish / Hindi |

The Nykaa cosmetics dataset (`CC0-1.0`) was checked and excluded: it is a product list with no review text.
BELIEVED, not verified: the English sets were scraped from Amazon and Sephora by third parties; the Kaggle
licence label is what they declare, and the underlying sites' terms were not read.

Pilot sample (seeded, `random.Random(42)`, after exact-text dedup and a 40 to 1,000 character filter):
75 reviews per stratum, 300 total. The vernacular stratum is OVERSAMPLED to a quarter of the pilot so that
agreement on Hinglish can be measured at all; its natural prevalence in marketplace review streams is far lower
(`docs/research/n5-research-gaps.md`: Hinglish 0 of 50 on Flipkart, 0 of 16 on Amazon.in), so no pilot rate
is a prevalence estimate. The pilot items are consumed by the pilot: a re-pilot draws FRESH items.

## 5. Agreement ceiling: the first gate (pre-registered)

Panel: four judges from four model families, run blind to one another, temperature 0, fixed seed, JSON output,
the same versioned prompt per task, on local Ollama (a Kaggle kernel or the local GPU):
`llama3.1:8b` (Meta), `qwen3:8b` (Alibaba), `gemma2:9b` (Google), plus a Mistral-family model. All are free.
These are SILVER labels: LLM consensus, correlated by shared pre-training data, not ground truth. A human
spot-check set would turn silver into gold; it is listed as a GG option, not a dependency.

Metrics, per task: Krippendorff's alpha (nominal for T1, T2, T4, T5 and the aspect-presence flags; ordinal for T3),
Fleiss' kappa, mean pairwise percent agreement, and the fraction of reviews with a CLEAR CONSENSUS (at least
three of four judges give the same answer; unanimity reported separately). Per class for T1: the confusion
between the judges' modal answers.

Gate, fixed in advance, per task:

| Result | Action |
|---|---|
| alpha >= 0.80 and consensus fraction >= 0.80 | Task passes; scale labelling |
| alpha 0.667 to 0.80, or consensus 0.65 to 0.80 | Usable with a caveat; labels used only where there is consensus (the rest is the model's abstain set); fix definitions where the confusions point |
| alpha < 0.667 or consensus < 0.65 | The task FAILS: rewrite the definitions from the confusion matrix, then re-pilot on FRESH items (at most two re-pilots); if still failing, merge confusable classes or drop the task from the model |

The stop rule for this session: after the first pilot, report and STOP before any labelling at scale.

## 6. Plan after the gate (outline; each step reports before the next)

A3 corpus and licences at scale; A4 labelling panel at scale, one judge-disagreement set kept for a human look;
A5 sealed train / validation / test split by product and by reviewer (no review of a product leaks across), with
a leakage ledger; A6 model: e5-base fine-tune on consensus labels, int8 export, thresholds recalibrated on int8;
A7 evaluation against plain-prompted Llama 3.3 70B and the current pipeline at matched coverage, precision at
coverage as the headline; A8 novel-issue detection with the Mahalanobis open-set scorer (E2: best AUROC).

## Amendments

### Amendment 1 (2026-10-10, before the pilot was run): aspect wording

An 8-item smoke test of the judge prompt on pilot items showed llama3.1:8b labelling every unmentioned aspect
`neutral`. The aspect instruction was reworded to "include an aspect ONLY if the review explicitly talks about
it" (prompt `ri-judge-v1`, unchanged name because no pilot result existed yet). The smoke labels were discarded.
Pilot 1 then showed that judges still over-assign aspects (see Amendment 2 and `docs/reports/review-intent-pilot1.md`).

### Amendment 2 (2026-10-11, S22 D3): evaluation protocol for every review-intent task, pre-registered

Registered BEFORE any labelling at scale and before any model is trained on review-intent labels. It restates the
decisions already taken (macro-F1 headline, per-class floor, precision at coverage) and fixes the rules that
were still open. It applies to every task that passes the re-pilot gate of section 5; a task that does not pass
is not modelled and is not claimed.

1. **Labels and their ceiling.** Training and test labels are the four-family panel's CONSENSUS (at least three of
   the valid judges agree, and at least 75 percent of them). Items without consensus are not labelled; they are
   the abstain set. The fraction of sealed test items WITH consensus (from the panel run on that set) is reported
   per task, and no coverage claim may exceed it. Every metric below is "against panel consensus" (silver) and is
   named that way in every table and sentence; "against the truth" requires the human audit offered in the
   pilot report and is stated as not done until it is.
2. **Sealed splits.** Train / validation / test are split by PRODUCT and by REVIEWER (no review of a product, and
   no reviewer, crosses a split), seeded (`random.Random(42)`), stratified by language and category. The
   sealed test manifest (ids and content hashes) is committed once and its hash is recorded in this file by an
   amendment before any model sees the validation data. Nothing in the test split is used to choose a prompt,
   a few-shot example, a threshold, a hyperparameter or a class definition.
3. **Leakage ledger, enforced in CI.** `reports/labelling/ledger.json` lists the content hash of every item that was
   ever used for anything other than final evaluation (prompt development, smoke tests, few-shot examples, the
   pilots, threshold selection, training). A unit test fails the build if (a) any test-manifest hash appears in
   the ledger, (b) any test-manifest hash appears in a training file's hash list, (c) the committed test-manifest
   hash differs from the one recorded here. Pilot 1 and the 8-item smoke items are entered in the ledger on creation.
4. **Metrics, per kept task (fixed).**
   - Headline: macro-F1 over the classes with at least 30 consensus items in the sealed test; classes below that
     are reported as "insufficient support" and are not scored or claimed. Accuracy is reported next to it, never
     instead of it.
   - Per-class F1 for every scored class, with a FLOOR of 0.60: a class below it is reported as a known weak class
     and is excluded from any product claim.
   - Precision at coverage (the product claim): the coverage at which precision reaches 0.95, and precision at
     coverage 0.9, 0.8 and 0.7, each with a 95 percent bootstrap CI that resamples PRODUCTS (clusters), not reviews.
     The confidence used is the engine's calibrated max-softmax (the value the serving API returns). The operating
     threshold for any claim is chosen on the VALIDATION split with the conservative rule (Wilson lower bound of
     precision clears 0.95), then applied once to the sealed test; the report gives the achieved precision and
     coverage and, from the split-half simulation, the share of splits that hit the target. Oracle (best-on-test)
     operating points are reported only as such.
   - The consensus-ceiling fraction from the panel (point 1).
   - Cost per 1,000 items and p50/p95 CPU latency of the int8 model (E3 method, thresholds recalibrated on int8).
5. **LLM comparison at MATCHED coverage.** On the same sealed items, plain-prompted Llama 3.3 70B (Groq, dedicated
   org, within the 100K tokens per model per rolling 24h ceiling, so the comparison is spread over days), and the
   current extraction pipeline, each with a verbalised confidence. Coverage is matched by choosing each arm's
   threshold to answer the same fraction of items (the fine-tuned model's coverage at its validated threshold);
   precision is then compared item-paired, with a paired bootstrap over products. A task SHIPS only if, at matched
   coverage, the fine-tuned model's precision exceeds the best LLM arm's with a CI that excludes zero, OR it is
   non-inferior (margin 0.02, CI lower bound above -0.02) at no more than 1/20 of the cost and 1/10 of the latency;
   and only if its scored classes clear the per-class floor. Otherwise it is reported as not shipped, with the numbers.
   If the sealed test is too small to separate the arms, the comparison is labelled underpowered, not "equal".
6. **Rating-text mismatch.** Defined as a deterministic rule (stars versus consensus sentiment; section 4 of the
   pilot report), not a judged task. Evaluated as precision and recall of the rule's `mismatch=yes` flag against the
   panel's consensus mismatch label on the sealed items, with product-cluster bootstrap CIs; the rule inherits
   sentiment's reliability and is shipped only if sentiment passes.
7. **Novel-issue detection.** Hold out whole intent / aspect groups at training (seeded draw, as in the engine's
   Track B), calibrate the unknown threshold on VALIDATION for 95 percent known-item retention on the int8 model,
   and report on the sealed test: rejection recall of held-out groups at that retention, known retention achieved,
   and AUROC, each with a CI, for Mahalanobis (the E2 winner), energy and calibrated softmax. A scorer ships only
   if its rejection recall exceeds calibrated softmax's with a CI excluding zero and known retention stays at or
   above 0.90.
8. **Languages.** Every language the product claims must have its own sealed-test slice with at least 100
   consensus items and its own row in every table; a language below that is not claimed. Transfer from English-only
   training is reported separately and is not used to support a claim for another language (E2: 0.639 versus 0.833).
9. **What would change these rules.** Only a dated amendment, written before the run it governs.

### Amendment 3 (2026-10-11, S22 C3c): definition fixes and re-pilot 1, registered before the re-pilot runs

Pilot 1 (`docs/reports/review-intent-pilot1.md`) passed no task. This amendment changes the definitions that
failed and fixes the re-pilot design. It is committed before any re-pilot item is shown to any judge. Prompt
version `ri-judge-v2`. The gate thresholds of section 5 are UNCHANGED.

**Definition changes (each traced to a pilot-1 finding).**

| Task | Pilot-1 finding | v2 definition |
|---|---|---|
| T1 intent | Most disagreement was `praise` against `suggestion`, `product_defect` or `pricing` (a mixed review forced into one label) | Priority rule: if the review contains any actionable complaint, request or question it is NOT `praise`; the primary intent is the highest-priority class present, ordered `return_refund_request`, `product_defect`, `delivery`, `customer_service`, `question`, `pricing`, `competitor_comparison`, `suggestion`, `praise`, with `spam_irrelevant` only when there is no product content. Secondary intents stay as before. |
| T2 sentiment | Best task; `neutral` never reached consensus | Unchanged (four values kept). |
| T3 urgency | `low` against `medium` dominated | Boundary examples added to the prompt: a wish or a subjective dislike is `low`; a concrete defect that stops normal use is `medium`; harm or an explicit refund / return demand is `high`. |
| T4 buy_again | 480 of the pairwise disagreements were `unclear` against `yes`; the explicit-`no` binary reached alpha 0.700 on the same labels | Replaced by `explicit_no_repurchase` (`yes` / `no`): `yes` only if the reviewer says they will not buy again or would not recommend it. Anything else is `no`. |
| T5 mismatch | Judged answers disagreed (qwen3 read the task differently); judges flagged 54 consensus mismatches, the star/sentiment rule 10 | Not judged. Derived: stars 4 or 5 with consensus sentiment `negative`, or stars 1 or 2 with consensus `positive`. The mismatch call is removed from the panel. |
| T6 aspects | Judges flag nearly every aspect (any-judge presence on 46 to 74 of 75 reviews per aspect, against consensus on 0 to 61) | Per category the five aspects with the highest pilot-1 consensus count: apparel `fit_size, style, fabric_quality, comfort, colour_accuracy`; beauty `results_efficacy, texture, skin_reaction, fragrance, value`; food `taste, value, ingredient_quality, efficacy_claims, quantity`. An aspect counts as present only with a QUOTE copied from the review; a quote that is not a substring of the review (case and whitespace insensitive) drops the aspect. Vernacular stratum: no aspects. |

A judge answer that fails to parse is retried once with a stricter reminder before it counts as missing (pilot 1:
mistral:7b failed 20 of 300 items, 6.7 percent).

**Re-pilot 1 design (fixed in advance).**
- Items (FRESH: none from pilot 1 or the 8-item smoke test; the pilot manifest is passed as `--exclude`):
  300 random items drawn exactly as in section 4 (75 per stratum, seed 43), plus 180 MINED candidates: 30 each for
  `delivery`, `customer_service`, `question`, `competitor_comparison`, `return_refund_request`, and 30 for `high` urgency
  (harm words), found by fixed regular expressions over the same four cleared corpora (the patterns are in
  `engine/labelling/mine.py`, committed with this amendment). Mined items are labelled `mined_<class>`: they enrich
  the rare classes but are NOT representative, so every metric is reported for the random 300 and, separately, for the
  mined 180; prevalence is never read off the mined items.
- Panel: the same four judges, blind, temperature 0, seed 42, one text call per item (no mismatch call).
- Success criteria per task, pre-registered: PASS or USABLE-WITH-CAVEAT under section 5 on the random 300.
  Rare-class support criterion: a class counts as measurable only with at least 30 consensus items across random plus
  mined items; otherwise it is reported as insufficient support.
- Decision rule: tasks that reach USABLE or better enter labelling-at-scale planning; tasks still FAILING after this
  re-pilot get one more definition fix (re-pilot 2 on fresh items) and are then merged or dropped. No labelling at
  scale starts before GG has seen the re-pilot report.
- The stars-versus-sentiment rule for T5 is evaluated on the random 300 only.

### Amendment 4 (2026-10-11, S22 M2): coarse taxonomy and re-pilot 2, registered before the re-pilot runs

Re-pilot 1 (`docs/reports/review-intent-repilot1.md`) failed the 10-class intent task (alpha 0.653, consensus 0.863, FAIL: below the 0.667 floor) and the
explicit-no-repurchase binary (alpha 0.487, FAIL), and showed that mining cannot fill the rare classes. BANKING77's 77 classes work because its
labels are clean; the bottleneck here is taxonomy ambiguity, not the model, so the taxonomy is coarsened before anything else
is spent. This amendment is committed BEFORE any re-pilot-2 item is shown to any judge. Prompt version `ri-judge-v3`.
The gate of section 5 is UNCHANGED (alpha 0.80 and consensus 0.80 to PASS; alpha 0.667 and consensus 0.65 for
USABLE-WITH-CAVEAT). Nothing about the gate is tuned in this amendment.

**Authorisation.** GG's S22 addendum M2 allows labelling at scale for any task that passes this re-pilot, so the Amendment 3
stop rule ("no labelling at scale before GG has seen the report") is lifted for tasks that reach PASS or USABLE-WITH-CAVEAT
here. A task that fails stays unlabelled. Two re-pilots is the section 5 limit; a task failing re-pilot 2 is dropped or
merged, not re-piloted a third time.

**New tasks (replace T1 and T4 for modelling; T1's 10 classes become a secondary, reported-not-claimed output).**

| Task | Values | Definition (decision rule) |
|---|---|---|
| `C1 needs_action` | `yes` / `no` | `yes` if the review contains at least ONE thing the seller can act on or owes a response to: a complaint about the product (defect, damage, not as described, adverse reaction, poor performance), the delivery (late, damaged parcel, missing item, courier), or the seller's service; an explicit return, refund or exchange request or action; or an unanswered question. `no` otherwise: praise, neutral description, a subjective taste or fit opinion with no fault stated, a price remark with no request, a wish or suggestion with no complaint, a comparison, spam. |
| `C2 broad_intent` | `product_issue` / `delivery` / `service` / `praise` / `other` | ONE value, by priority when several are present: `product_issue` (the product is faulty, broke, damaged, not as described, caused a reaction, or underperforms), then `delivery` (shipping, courier, timing, parcel condition, missing items, rider behaviour; praise of fast shipping alone is not `delivery`), then `service` (how the seller or support handled a contact, and return / refund / exchange handling or requests), then `praise` (satisfaction with NO complaint, request or question of any kind), else `other` (price remarks, suggestions, comparisons, questions with no complaint, neutral descriptions, spam or off-topic). |

Boundary examples shown to the judges (copied from real corpus reviews in the spec's section 2 table or invented and marked):
"fell apart in the wash, returning" -> `needs_action=yes`, `product_issue`; "love it, but the pump broke on day two" ->
yes, `product_issue` (a complaint outranks praise); "it is overpriced for a shirt" -> no, `other`; "I wish it came in blue" -> no,
`other`; "does it contain nuts?" -> yes (a reply is owed), `other`; "great product, arrived fast" -> no, `praise`; "parcel was
crushed but the product is fine" -> yes, `delivery`; "seller never replied to my message" -> yes, `service`.
Fine intents (`pricing`, `suggestion`, `question`, `competitor_comparison`, `customer_service` as distinct from
`return_refund_request`) return only where a later re-pilot shows agreement; none is claimed now.

**Kept tasks (definitions as in Amendment 3, unchanged).** `T2 sentiment` (four values), `T3 urgency` (with the v2 boundary
examples), and `T6 aspect sentiment` for beauty and food only, still requiring a verifiable quote (a quote that is not a
substring of the review drops the aspect). Aspect presence is not a claim; aspect sentiment is scored only on aspects that at
least two judges quoted. Apparel aspects are dropped (borderline in re-pilot 1) and the vernacular stratum has no aspects. The
explicit-no-repurchase flag is dropped from the panel (FAIL in re-pilot 1; a rare flag needs its own gate, which is not
registered here). T5 mismatch stays derived from stars and consensus sentiment.

**Re-pilot 2 design (fixed in advance).**
- Items: FRESH, `--exclude` pilot 1 and re-pilot 1 manifests (the 8 smoke items are inside the re-pilot-1 manifest): 300 random
  items drawn as in section 4 (75 per stratum, seed 44), plus 60 MINED candidates (30 `delivery`, 30 `service`, by the fixed
  `delivery` and `customer_service` regexes already in `engine/labelling/mine.py`) to test whether the two thinnest broad classes become measurable. Mined items
  are reported separately and never used for prevalence.
- Panel: the same four judges, blind, temperature 0, seed 42, one text call per item, one parse retry; Kaggle T4s.
- Success criterion per task, on the random 300 only: PASS or USABLE-WITH-CAVEAT under section 5. Tasks: C1, C2, T2, T3,
  T6 sentiment (beauty, food).
- Reported per task: pairwise agreement, Krippendorff alpha (nominal; ordinal for T3), Fleiss kappa, clear-consensus fraction
  (the coverage ceiling), unanimity, and for C1/C2 the consensus class distribution with Wilson 95 percent intervals.
- Pre-registered expectation (a hypothesis from the exploratory look at re-pilot 1, which does not count as evidence): C1 reaches
  at least USABLE. A result that disagrees is reported as it is.

**Rare-class sample size rule (M2d, computed from re-pilot 2, rule fixed now).** For each class with consensus prevalence p
(Wilson interval on the random 300): the test positives needed for a per-class precision interval of half-width 0.05 at
precision 0.90 are m = 138 (normal approximation, 1.96 squared times 0.09 over 0.05 squared); the labelled corpus needed is
N = m / (p * 0.15) for a 15 percent sealed test share, reported at p's point value and at its lower and upper bounds. A class
is "measurable now" if it has at least 30 consensus items across random plus mined items (the Amendment 3 criterion) and
"stable" if N is at most the size of the corpus we can label (10,000 to 20,000 items).

**Labelling at scale, if a task passes.** 10,000+ items, stratified (language and category), drawn fresh from the four cleared
corpora and excluding every id in the ledger; the sealed split, leakage ledger, metrics and LLM comparison are exactly
Amendment 2, unchanged. Panel consensus is the label; items without consensus are the abstain set. Production reviews are not
used.

### Amendment 5 (2026-10-11, S22 M2e): two-judge training labels, four-judge sealed test, ~5,000 items; registered before any scale labelling

Re-pilot 2 (Amendment 4) found no PASS and five USABLE-WITH-CAVEAT tasks and measured cost: 14.4 GPU-seconds per item for
four judges, 7.1 for two judges plus a tiebreaker on disagreement. GG accepted the re-pilot and set the rules below.

**Labelling rule.**
- TRAINING and VALIDATION labels: judges `llama3.1` and `qwen3` label every item. For each task separately, if both parse and
  agree, that value is the label. If they disagree (or one failed to parse) and `mistral` was run on the item, the label is
  mistral's value when it equals one of the two, otherwise the item is in the abstain set for that task. Mistral runs on an
  item only if at least one task disagrees (one call per item, all tasks). Gemma is not run on training items.
- SEALED TEST SET (15 percent of items, drawn by the id-hash split of Amendment 2): all four judges, label by the existing
  consensus rule (`consensus_label`: a clear majority; otherwise abstain). No human audit is part of the pipeline, by GG's
  standing rule that no workflow blocks on him labelling. The four-judge panel is the check on the training-label rule.
- Consequence stated now: training labels are SILVER from a cheaper rule than the test labels. Agreement of the two-judge rule
  with the four-judge rule is measured on the sealed set and reported, not assumed. That comparison is not independent
  (the two-judge labels are a subset of the four-judge votes), so it is reported as a consistency check only.

**Size.** About 5,000 items (the re-pilot 2 sample-size rule gives about 4,700 for 138 `needs_action` test positives at a
prevalence of 0.194 and a 15 percent test share). One pass labels every task at once. Stratified by language and category as
Amendment 4 specified, drawn fresh, excluding every id in the ledger and every earlier manifest. Hinglish is capped by the
corpus (393 rows, 225 used in pilots) and is reported as such.

**Gate unchanged.** PASS and USABLE-WITH-CAVEAT thresholds are those of section 5, applied to the four-judge agreement
statistics measured on the sealed set. Supplementary, never used to redefine PASS: Gwet's AC1 and raw percent agreement for
skewed binaries (`needs_action` is about 80 percent "no", where alpha and kappa are known to understate agreement).

**Classes without data.** `delivery` and `service` had 4 consensus items each in re-pilot 2. They are reported as
"insufficient data" (fewer than 20 sealed-test positives) until a licensed shopping-app review source fills them; the source,
its licence text and expected per-class yield are recorded in a dated report before any of its rows are labelled.

**Evaluation, as Amendment 2, plus.** The classifier is `intfloat/multilingual-e5-base` with one head per task. On the sealed
four-judge set: precision at coverage with the CONSERVATIVE rule (the threshold is chosen on validation as the lowest
confidence whose Wilson lower bound for precision reaches the target; the oracle threshold is also reported and labelled as
oracle), macro-F1, per-class F1 with the Amendment 2 floor, the consensus ceiling, bootstrap and Wilson intervals, and plain-
prompted LLMs at matched coverage. Anything not in this list is exploratory and labelled so.

### Amendment 6 (2026-10-12, S23 6a): the safety stratum, a separate enriched sealed stratum; registered before any model is evaluated on it

The Amendment 5 sealed set holds too few `urgency=high` items to measure a safety classifier. A keyword miner fixed before any
label existed (`engine/labelling/mine_safety.py`) produced candidates, and the four-judge panel (Amendment 5) labelled 1,800 of
them (`reports/labelling/safety/panel_report.json`, source of every count below): 365 have consensus `urgency=high`.

**What the stratum is.** A separate, ENRICHED, keyword-mined stratum, disjoint from the Amendment 5 sealed set (candidates exclude
every ledger entry, every `sealed_excluded` hash, and anything that near-duplicates or quotes a sealed item). Labelled by the
four-judge panel with `consensus_label` on every item, train items included. It is reported separately and NEVER pooled with the
base-rate numbers of Amendment 5: the miner's recall is unmeasurable and a precision measured here is not a base-rate precision.
It supports precision and recall of the `urgency=high` decision among safety-language reviews, not prevalence or deployment rates.

**Split.** A pure function of the content hash with its own salt, `review-intent-safety-split-v1`, same construction as
`scale_sample.split_of`: test share 0.40; of the rest, 0.10 is validation, the remainder training. Test is 0.40 (not 0.15)
because 0.15 would give about 55 sealed positives against the 138 that Amendment 5 set as the minimum for a usable interval.

**Expected values, stated before the split is computed.** The hash is independent of the label, so the sealed positives are
Binomial(365, 0.40): expected 146.0, standard deviation 9.4. The probability of fewer than 138 is about 0.18 (normal approximation
with continuity correction). Train plus validation expected 219. A realised count below 138 is reported plainly with the interval
it supports (at n = 138 and p = 0.5, a Wilson half-width of about 0.083); the bar is not lowered and the salt is not changed.
The split is computed once and committed as ids and hashes only.

**Leakage.** The safety test hashes form a second sealed list checked by the same CI test as the first (`leakage_ledger`: ledger
overlap, digest, tracked-file scan, near-duplicate and quote scan). Safety train and validation hashes enter the ledger as used
items. Any safety train or validation item that near-duplicates a safety test item is dropped from training and listed.

### Amendment 7 (2026-10-12, S23 run 3): training with the safety stratum, registered before any run-3 training step

Run 3 asks one question: does adding the safety stratum's TRAIN items to training raise `urgency=high` recall on the sealed safety
stratum without degrading the Amendment 5 sealed set. Nothing else changes. No food-delivery or other auxiliary-domain data is used;
`delivery` and `service` stay "insufficient data" (the safety train items carry 2 and 3 such labels, which does not change that).

**Training data.** Run 2's train items (3,777) plus the safety TRAIN items of `reports/labelling/safety/split.json` (787 items,
157 `urgency=high`; `dropped` items are excluded). Safety labels are the four-judge consensus of Amendment 6 (stronger than the
two-judge base train labels; stated, not corrected), for `needs_action`, `broad_intent` and `urgency`; an abstention, the whole
`sentiment` task and all aspect tasks are masked (-100), as in run 1/2. Safety VAL items (87, 17 high) and TEST items never enter
training; any id in a val/test list or a sealed list aborts the run (`leakage_ledger check` must report ok first). Review text is read from the
private `D:/ml-cache/s23/safety_items.jsonl` and is never written to the repo.

**Hyperparameters = run 2.** Seed 42, `multilingual-e5-base`, lr 5e-5, batch 32, max length 128, frozen embeddings, up to 30 epochs,
patience 3, linear warm-up/decay over the epoch budget, no class weights. Early stopping and the per-task temperatures use the
Amendment 5 VALIDATION set exactly as run 2 (mean macro-F1 over tasks with validation labels), so the stopping rule and the
base-set calibration do not move. The safety validation items are held out of that rule and used only for the `urgency=high`
decision threshold below. Consequence: the stopping epoch is not selected for safety recall; if that is the weak point it is reported, not tuned.

**Control (environment).** Run 2 ran on Kaggle (torch 2.11); run 3 runs on the local RTX 3070 (torch 2.6, a different machine).
To separate environment from data, the run 2 recipe is re-run locally with NO safety data (`run2_local`, seed 42) and reported beside run 2 and run 3.
If `run2_local` differs from run 2 by more than 0.02 macro-F1 on any core task, the verdict is labelled "environment-confounded".

**Evaluation A: Amendment 5 sealed set (n = 757 after ledger exclusions), all four core tasks plus aspects, exactly the run 1/2
pipeline** (`engine.intent.evaluate`, 1,000 bootstrap resamples, seed 42, item bootstrap; the run 1/2 product-cluster intervals are produced too).
Run 3 is compared with run 2 on the SAME items: paired item-bootstrap 95 percent CI of the difference in macro-F1 per core task.
A difference is "outside noise" only if that CI excludes 0. Seed-to-seed variance is NOT measured (one seed each), so "inside noise" means
"not distinguishable from item sampling noise", and "outside noise" does not exclude seed noise.

**Evaluation B: the safety sealed stratum, reported SEPARATELY and labelled "enriched, keyword-mined, silver (panel) labels".** Never pooled
with A and not a base-rate or prevalence number. Test = `split.json` test items minus the ledger's `sealed_excluded` hashes. The binding n
for `urgency=high` recall is the number of INDEPENDENT positive groups (near-duplicate groups at Jaccard 0.9, the construction of
`safety_split.neardup_clusters`): 131 before ledger exclusions. The nominal item count (143 before, 142 after exclusions) is shown only beside it.
Each group counts once: a group's hit is the share of its positive members flagged; Wilson 95 percent intervals use n = number of groups and k = the
rounded group-hit sum; the bootstrap resamples groups (the item bootstrap is not used because it would treat near-duplicates as independent). Reported: recall, precision
(group-weighted, 1/group size per item) of the decision `urgency = high`, with Wilson 95 percent CIs, at two operating points: (P) the argmax of the
validation-calibrated probabilities (PRIMARY); (S) a threshold on p(high) chosen on safety VALIDATION only, maximising F1 of the high class
(17 positives: noisy, secondary). The same procedure is applied to run 2 and `run2_local`, which never saw safety data.
Run 2 vs run 3 on the stratum is a paired group-bootstrap difference.

**Int8.** The six run-2 CPU variants are re-exported from run 3's checkpoint; temperatures are refit and thresholds re-picked on VALIDATION only, and CPU latency
is measured exactly as run 2 (`engine.intent.latency`, same local CPU).

**Success criteria (fixed now).** Run 3 is PROMOTED only if BOTH hold:
1. A (non-degradation): for each of the four core tasks, the point difference in macro-F1 versus run 2 is at least -0.02;
2. B (safety gain): at operating point (P), `urgency=high` recall on the stratum is higher than run 2's by a paired group-bootstrap 95 percent CI
   that excludes 0, and group-weighted precision is not lower than run 2's by more than 0.05.
Otherwise run 3 is reported as a NEGATIVE RESULT with the same tables; no threshold, tolerance, epoch count or seed is changed afterwards, and
no second run is made to rescue it without a new amendment. Whatever the outcome, `docs/STATUS.md` is not updated by this run and nothing is deployed.

### Amendment 8 (2026-10-12, S23 run 3, seed level): registered before any seed run

Why: Amendment 7 ran one seed per arm. A control re-run of run 2 on the same machine (`run2_local`) differed from the Kaggle run 2 by
more than 0.02 macro-F1 on a core task and its argmax recall on `urgency=high` was 0.319 against 0.119: seed/environment noise is as large as
every difference seen. No conclusion is drawn from single runs. This amendment replaces them with a seed-level comparison. It does not change
Amendment 7's data, recipe, early stopping or evaluation sets.

**Design.** Arm A = run 2 recipe, `--safety-stratum exclude`. Arm B = run 3 recipe, `--safety-stratum include`. Both arms: seeds 42, 43, 44, 45, 46
(the same five seeds, so differences are paired by seed), up to 30 epochs, patience 3, lr 5e-5, batch 32, early stopping on base validation only,
local RTX 3070 (CUDA fp16 autocast), one commit for all ten runs. Each run is also exported to the six CPU variants of Amendment 7.
If a run fails or a seed is dropped, it is dropped from BOTH arms and said so. No latency re-measurement (the serving path does not change the compute).

**Decisioning (serving default).** Rare classes are decided by a per-class threshold on the calibrated probability, never argmax: the threshold
maximises F1 of that class on VALIDATION only (ties to the higher threshold; the rule of `safety_eval.f1_threshold`), per task, per model variant (int8
variants refit it, as they refit temperatures). Rare classes: `urgency=high` only; a rare class with fewer than 5 validation positives falls back to
argmax and is flagged. Every other class is decided by argmax among the non-rare classes. Argmax for all classes stays available only as an explicit
opt-in and is reported as an ABLATION, never as a result. Thresholds for Evaluation A and the serving default (point D) are fitted on the
Amendment 5 validation set; point S (safety validation, Amendment 7) is kept.

**Reported (all on the 5 seeds, per arm and per CPU variant).** Per-seed values, mean, sd, and the 95 percent t-interval over seeds (df = 4).
For differences B - A: the per-seed paired difference, its mean, sd and 95 percent t-interval. Metrics: Evaluation A macro-F1 per core task
(default decisioning; argmax ablation beside it); Evaluation B `urgency=high` recall and precision at D, S and (ablation) P, with the 130
independent positive groups as the binding n of each seed's estimate, labelled "enriched, keyword-mined, silver (panel) labels". Within-seed
Wilson/bootstrap intervals are shown in the per-seed artifacts only; conclusions use the seed level.

**Decision rule (fixed now), fp32 GPU model, point S for Evaluation B.** Arm B (run 3 recipe) is PROMOTED only if ALL hold:
1. the 95 percent t-interval of the paired B - A difference in safety recall at S has a lower bound above 0;
2. the mean B - A macro-F1 difference is at least -0.02 on EVERY core task (needs_action, broad_intent, sentiment, urgency), default decisioning;
3. the 95 percent t-interval of the paired B - A difference in precision at S has a lower bound of at least -0.05 (it excludes a drop larger than 0.05).

NOT PROMOTED if criterion 2 fails, or the recall interval's upper bound is at most 0, or the precision interval's upper bound is below -0.05.
Otherwise INCONCLUSIVE (reported as such; no further seeds without a new amendment). Conclusions are stated only at the seed level; any single
seed's numbers, and every argmax-at-P number, are reported as ablation/descriptive only. Nothing is deployed and `docs/STATUS.md` is not touched.

### Amendment 9 (2026-10-12, S23 moat): the `gpt-oss-20b-local` arm; registered before any local row exists

Numbering note: this block was first drafted as "Amendment 7" on its branch; Amendments 7 and 8 were taken by run 3 before it merged, so it is Amendment 9. Registration commit (before any local row existed): bb676e6.

Context: the LLM-vs-encoder comparison protocol is the docstring of `engine/intent/llm_moat.py`. Its Groq arms share an org with the
production failover (evals must stay off that pool) and the benchmark key now returns HTTP 401. GG decision: the gpt-oss-20b arm is
RESTARTED ENTIRELY on free Kaggle GPUs with local inference. The 120b arms stay on Groq and are not changed. The 502 existing Groq
20b rows are kept read-only as a cross-check; Groq and local rows are NEVER mixed inside one arm. Local rows are an INFRASTRUCTURE
change (runtime, quantisation, sampling implementation), not a replication of the Groq rows; the cross-check measures that difference.

- Arm name `gpt-oss-20b-local`; output stem `openai_gpt-oss-20b__local` (appears in `compare()` as the 20b zero-shot arm).
- Same `PROMPT`, same four tasks, same `sealed_ids()` order, same row schema `{id, ans, tokens}`, same scoring (`llm_arrays`,
  `SEL.summarize`, `decide`). Request: temperature 0, seed 42, max_tokens 600 (counting reasoning tokens, as on Groq), and
  `response_format={"type": "json_object"}`. Parse: `json.loads(content)`; if that fails, the outermost `{...}` substring is tried
  once (the runtime may emit text around the JSON); otherwise the answer is unparseable.
- Reasoning effort: the Groq arm never set it, so it ran at the provider default (not knowable beyond that). The local arm also
  sets none and runs the model default. No tuning of any kind.
- Stack: Ollama (latest linux-amd64 release at run time; version recorded in the run's `status.json`), model tag `gpt-oss:20b`
  (OpenAI release, MXFP4 weights, 13,793,422,816 bytes). Registry manifest of 2026-10-12: manifest sha256
  `f38aa0c53da5f8c49d08c43a99df24ff53167fe68e24664a7777288e7656fdfe`, weights blob
  `sha256:8ecb5d4ad6172c63f9cbf1a07a04e525985a8a11a6e516da02b674c4010de716`. The digest actually pulled is recorded and must match;
  a mismatch is reported, not hidden. Context 4096. Two T4s, one server per GPU, items split by parity, sequential per server.
- Stopping rule exactly as for the other arms: the first 300 sealed items (sealed order) are run, `decide()` is applied once on
  them, and the run continues to all sealed items unless all four tasks are decided.
- Unparseable output counts as wrong at confidence 0 (kept). Runtime errors (connection, 5xx, timeout) are retried and never
  recorded as answers; a non-200 other than 5xx is also not an answer.
- Smoke test: the first 20 sealed items are run once to measure throughput and to check that the runtime honours JSON mode. Smoke
  rows are discarded and never scored. If more than 2 of 20 contents are empty or not JSON, the arm is rerun WITHOUT
  `response_format` using the extraction rule above, and that change is reported.
- Cross-check against the Groq 20b rows on the overlapping ids: per task label agreement, Cohen's kappa, Spearman correlation of
  confidences, with n and item-bootstrap 95 percent CIs. No gate depends on it.


### Amendment 10 (2026-10-17, S24 moat): the `gpt-oss-20b-local-fewshot` arm; registered before any few-shot local row exists

Context: Amendment 9 ran gpt-oss-20b zero-shot on local inference. The Groq 120b few-shot arm exists in the protocol (`llm_moat`
FEW-SHOT ARM); this amendment adds the matching few-shot arm for 20b on the same local stack, so that "does worked-example
prompting close any of the gap to the encoder" is answered on one runtime. Registration commit (before any few-shot local row
existed): recorded in the PR description and in `reports/intent/moat_local20b_fewshot/run_status.json`.

- Arm name `gpt-oss-20b-local-fewshot`; output stem `openai_gpt-oss-20b__local_fewshot`.
- Model, stack, sampling and request exactly as Amendment 9 (Ollama `gpt-oss:20b`, manifest sha256
  `f38aa0c53da5f8c49d08c43a99df24ff53167fe68e24664a7777288e7656fdfe`, context 4096, temperature 0, seed 42, max_tokens 600, JSON mode,
  two T4 servers, items split by parity, default reasoning effort). The Ollama version and digest actually used are recorded and a
  mismatch is reported, not hidden.
- Prompt: the existing `PROMPT` with the `shots_text` block inserted before `Review:` exactly as `llm_moat.ask` does for the
  Groq few-shot arm. Example confidences are the fixed value 90 that `shots_text` writes today. KNOWN LIMITATION, not tuned: the
  constant may pull verbalised confidences towards 90 and so distort calibration, which is the quantity the coverage-at-precision
  metric measures; the result is read with that in mind.
- Shot selection: `llm_moat.pick_shots(scale.jsonl, n_max=8)` unchanged (training split only, review at most 220 characters, all
  four labels present, `random.Random(42)` shuffle, greedy cover of unseen task/class pairs). It returned 7 shots (the greedy cover
  is exhausted after 7); they cover 13 of the 14 task/class pairs. NOT covered: `broad_intent=service`. This is reported, not
  repaired. Shot ids (ids only; all have `split == "train"` in the scale manifest and labels in `labels.json` train):
  `scale-food-0367`, `scale-food-1219`, `scale-food-1341`, `scale-food-0839`, `scale-vernacular-0030`, `scale-apparel-0890`,
  `scale-vernacular-0083`. The order is the selection order and is the prompt order. `urgency=high` appears once.
- Leakage checks, run before this registration on the private texts: none of the 7 shot ids is in `sealed_ids()`; none of the 7
  content hashes (`text_sha(clean(text))`) is in `leakage_ledger.sealed_hashes()` (scale test plus safety test) or in
  `leakage_ledger.exclusions()`; each shot's hash equals the scale manifest hash for its id; against the 1,483 committed sealed
  fingerprints of `reports/labelling/sealed_fingerprints.json` no shot reaches the near-duplicate rule (4-character Jaccard at least
  0.9) nor the quoted-substring rule (12-character containment at least 0.9); the maxima were far below both thresholds.
- Token budget: the full prompt (shots plus a review truncated to 1500 characters) plus up to 600 completion tokens must fit in the
  4096 context. Measured on the smoke run from the server's `prompt_eval_count`, reported. Decided BEFORE the full run: if the
  smoke maximum of prompt tokens plus 600 exceeds 4096, the arm drops to the first 5 shots in order and the smoke is repeated; that
  change would be recorded in the run status.
- Same sealed order, same stopping rule (the first 300 sealed items are run, `decide()` is applied once, the run continues to
  all sealed items unless all four tasks are decided), same handling of unparseable output (wrong at confidence 0, kept) and of
  runtime errors (retried, never recorded as answers). Smoke test of the first 20 sealed items first; those rows are discarded and
  never scored; JSON-mode fallback rule as Amendment 9.
- Comparison targets: few-shot local vs zero-shot local (matched ids, paired differences of the same statistics) and vs the
  encoder, on the same labels (SILVER, four-judge panel consensus). No gate depends on the Groq cross-check.

Implementation note (added with the code, still before any few-shot local row exists; no design change): `llm_moat.ask` builds a
few-shot prompt by `str.replace` followed by `str.format`, which cannot work when the shots block contains the JSON braces that
`shots_text` writes. The local runner therefore renders the prompt as head, shots block, `Review: <text up to 1500 characters>`,
tail. The model sees the same text order as the specification above; with no shots the zero-shot prompt is byte-identical to
Amendment 9 (unit-tested). The few-shot rows carry an extra `ptoks` field in the shard files (the server's prompt token count) and
`merge` drops it. The smoke also probes the longest and the most non-ASCII sealed reviews with the server prefix cache defeated
(a leading marker line, which makes the count an upper bound) to apply the token budget rule above.

Smoke result and decisions (recorded after the two discarded 20-item smokes and before the full run; no few-shot row has been scored).
- Context budget: the largest prompt measured was 1143 tokens (cache-defeating probe on the ten longest or most non-ASCII sealed
  reviews; the longest sealed review is under 1000 characters), so prompt plus 600 completion tokens is far below the 4096 context.
  All 7 shots are kept; the reduction to 5 shots is not triggered.
- JSON-mode rule: the first smoke (with `response_format`) returned 3 of 20 empty or unparseable answers, which is more than 2 and so
  triggers the Amendment 9 fallback. All 3 completions stopped at exactly 600 completion tokens (reasoning consumed the budget, so the
  content was empty): a token-cap truncation, not a JSON-mode non-compliance. As registered, the smoke was repeated WITHOUT
  `response_format`: the same 3 items failed and every item's token count was identical to the first smoke, i.e. on this Ollama
  build the request flag had no measurable effect. The registered rule is applied literally: the full run uses no `response_format`
  and the extraction rule. The 600-token cap is NOT raised (same as the zero-shot arm); the truncation rate is reported and those
  items score as wrong at confidence 0, which penalises the few-shot arm in a way the shots themselves do not cause.
- Runtime: the smoke servers ran Ollama 0.40.5 (the zero-shot arm ran 0.40.3, "latest at run time"); the model digest equals the
  registered manifest sha256. The version difference is disclosed in the report.


### Amendment 11 (2026-10-17, S24 moat compare report): decision rule and matched-coverage comparison; registered before the 20b compare numbers were computed

Context: three arms now have answers on the same sealed ids: the encoder, `gpt-oss-20b-local` (zero-shot, Amendment 9) and
`gpt-oss-20b-local-fewshot` (Amendment 10, stopped at the first 300 sealed ids by the registered rule). This amendment fixes how they are
compared, before any encoder-vs-20b number under THIS rule exists. Disclosure of what was already visible: the few-shot-vs-zero-shot
paired differences (`moat_local20b_fewshot/paired_vs_zeroshot.json`) and the `decide()` intervals at n = 300 were published before this
amendment; the Wilson-lower-bound operating points and the three-way paired bootstrap defined below had not been computed. Nothing is
tuned after seeing results; if the rule turns out badly chosen, that is reported, not repaired.

- Labels and ids. Gold is the four-judge panel consensus (SILVER), not human truth. MATCHED ids, per task: the first 300 ids of
  `sealed_ids()` (sealed order) that every compared arm has a row for and that have a gold label for that task and an encoder
  prediction. Unparseable or empty LLM answers stay in the denominator, wrong, at confidence 0 (as in Amendments 9 and 10).
- Encoder: the run-2 model (`reports/intent/run2`, the checkpoint behind `model_report.md`; run 3 was a negative result and is not
  promoted). Label = argmax of the temperature-scaled probabilities stored in the pred files; score = their maximum. The same
  confidence-ordered rule below is applied to every arm, which makes the encoder number an in-sample operating point like the others.
  As a supplementary, deployable reading the encoder is also evaluated at the conservative threshold chosen on VALIDATION and stored
  in `eval_run2.json` (`selective.conservative_p0.95.threshold`), applied once to the matched ids.
- (a) Operating point per arm and task: among thresholds equal to an observed confidence value, take the one with the HIGHEST coverage
  such that the Wilson 95 percent (z = 1.96) LOWER bound of precision among answered items is at least the target (0.95). Answered =
  items whose confidence is at least the threshold (tie groups are therefore always included whole); precision = correct / answered;
  coverage = answered / n matched. If no threshold qualifies the coverage is 0 and that is stated ("no qualifying threshold").
- (b) Matched-coverage comparison: per task, c* = the smallest NON-ZERO operating-point coverage among the compared arms (all arms
  zero: not computed). Each arm is read at the largest whole-tie-group answered set containing at most ceil(c* x n) items (if the top
  tie group alone is larger, that top group, flagged as overshoot). Reported: each arm's actual coverage, precision, and Wilson 95
  percent interval, so an arm that answers fewer items is not rewarded. Descriptive; no verdict is drawn from this block alone.
- (c) Paired item bootstrap: seed 42, 2000 resamples, items resampled once per replicate and the same index set used for every arm
  (per task). In each replicate the operating point of (a) is recomputed for every arm. Quantities: coverage at the operating point
  and plain accuracy (all matched items), differences for each pair of arms; percentile 95 percent intervals (2.5 and 97.5).
  Pairs: encoder vs zero-shot, encoder vs few-shot, few-shot vs zero-shot.
- (d) Verdicts. For a pair and a task, A "beats" B on the operating-point coverage only if the bootstrap interval of (coverage A minus
  coverage B) excludes 0 on A's side AND the point operating-point coverages differ in the same direction. A "beats" B on accuracy only
  if the bootstrap interval of the accuracy difference excludes 0 on A's side AND the Wilson-lower-bound operating-point coverages
  also favour A (A strictly higher). Anything else is "not separated" (this includes the case where one criterion holds and the other
  does not; the report prints both so the reader can see which failed).
- Unmatched reading (labelled as such): the zero-shot local arm on ALL sealed ids it covers vs the encoder on the same ids, same rules.
  It is not used for the matched verdicts.
- Cross-check (never mixed into any verdict): the 502 Groq gpt-oss-20b rows, read read-only, on the matched ids they cover, with the
  same functions, beside the local zero-shot arm.
- The gpt-oss-120b arms are not part of this report (the benchmark key returns HTTP 401); no workaround is attempted.
- Stated confounds, to be repeated in the report: n = 300 is small; verbalised confidence is coarse and 35 percent of few-shot
  confidences equal exactly 90 (copied from the shots); 27 of 300 few-shot and 6 of 300 zero-shot answers are empty or unparseable
  (600-token cap spent on reasoning) and count wrong at confidence 0; the two local arms ran different Ollama builds (0.40.5 vs 0.40.3);
  the Groq rows differ in infrastructure (runtime, quantisation, sampling).

### Amendment 12 (2026-10-17, S25 moat): re-run of the 20b local arms with a token budget that does not truncate; registered before any v2 row exists

Context: in Amendment 10's run 27 of 300 few-shot completions (and 6 of 300 zero-shot) ended at the 600-token cap with empty content,
because gpt-oss spends completion tokens on reasoning before it answers. Those items score wrong at confidence 0, so the Amendment 10
conclusion "few-shot is worse than zero-shot" mixes worse answers with empty answers and is NOT a finding about worked examples. This
amendment re-runs both arms with settings chosen only by an empty-completion canary. The Amendment 10 and Amendment 9 results stay on
file unchanged and are labelled "cap 600" wherever they are cited.

- Canary (before any scored row): the few-shot prompt on the first 30 sealed items; the rows are discarded and only counts are kept. A
  fixed ladder, stopping at the first rung with 0 empty or unparseable answers of 30: L1 reasoning effort low, max_tokens 1500; L2 low,
  2500; L3 medium (the model default), 2900. Context stays 4096 (largest measured prompt 1143 tokens, so 1143 + 2900 fits). If no rung
  reaches 0, the arm is reported as blocked, with the three rungs' counts, and nothing is run. The request field is `reasoning_effort`;
  whether this Ollama build honours it is read from the mean completion tokens of L1 against L3 and reported (not assumed).
- v2 runs, same model digest, Ollama build as installed at run time (recorded), temperature 0, seed 42, no `response_format` (as
  Amendment 10), same 7 shots, same sealed order, with the canary's settings: (a) the few-shot arm under the Amendment 10 stopping rule
  (n=300, then on unless all four tasks are decided), stem `openai_gpt-oss-20b__local_fewshot_v2`; (b) the zero-shot arm on the first 300
  sealed items with IDENTICAL settings, stem `openai_gpt-oss-20b__local_v2`, so that the comparison varies only the shots.
- Reported beside every result: number of empty or unparseable answers (and, for rows carrying `fin`, how many ended at the cap).
- Comparisons: PRIMARY, few-shot v2 vs zero-shot v2 on matched ids (paired item-bootstrap, same statistics as Amendment 10); SECONDARY,
  each v2 arm vs the encoder with the Amendment 10 interval rule; the v2 zero-shot vs the cap-600 zero-shot is reported as the size of the
  truncation artefact. A difference is called only if the interval excludes zero. Labels are SILVER (four-judge panel consensus).
- Not changed: shots, prompt, scoring, sealed set, the encoder's numbers. Amendment 11 (the compare rule) is registered separately.

### Amendment 13 (2026-10-18, S26 moat): pin one Ollama build; test whether `reasoning_effort` is honoured; registered before any effort-test row exists

Context: the 20b arms ran on three Ollama builds (`run_status.json`): cap-600 zero-shot 0.40.3, cap-600 few-shot 0.40.5, and BOTH v2 arms 0.40.6.
The kernel downloaded "latest" at run time, so the build was not controlled. Separately, Amendment 12's canary stopped at its first rung (low,
1500) with 0 empties, so the ladder's L1-vs-L3 token comparison that was to show whether the build honours `reasoning_effort` never ran: "low
effort" is a requested setting, not a demonstrated one. This amendment fixes the first and measures the second. Nothing already reported
changes; statements are restated below.

- Pin (code change, no row yet): `engine/kaggle/kernel_moat20b.py` downloads the release asset
  `https://github.com/ollama/ollama/releases/download/v0.40.6/ollama-linux-amd64.tar.zst` and refuses to continue unless its sha256 is
  `c164ab8b1b28e2a247c29719b8e404c423c5b83ae64c3ab4e59d50c7ff7fc5e0` (the digest GitHub publishes for that asset). 0.40.6 is the build both
  v2 arms ran on. Every later kernel run records the server version and the digest it verified.
- Effort test (new kernel mode `effort`, discarded rows, counts only): the ZERO-SHOT prompt on the first 20 sealed items, model
  `gpt-oss:20b`, temperature 0, seed 42, no `response_format`, `max_tokens` 2900 (largest measured prompt 1143, so it fits the 4096 context),
  at four settings on the same 20 items: no `reasoning_effort` field (server default), `low`, `medium`, `high`. Completion tokens of a call
  = server `total_tokens` minus server `prompt_tokens`. Reported per setting: mean, median, max completion tokens; calls ending at the cap
  (`fin == length`); empty or unparseable answers.
- Criterion, fixed now: `reasoning_effort` is HONOURED iff BOTH (a) the paired two-sided exact sign test on completion tokens, high versus
  low over the 20 items (ties dropped), has p < 0.05, and (b) the median completion tokens at high is at least 1.25 times the median at low.
  Medium and the default are reported as context, not part of the test. If either part fails the field is NOT demonstrated to be honoured.
- If honoured: "low effort" in Amendment 12 is a demonstrated setting. If not honoured: the v2 arms ran at the build's own reasoning depth
  with a 1500-token cap, the word "low" is removed from every statement about them, and what survives is exactly: the v2 few-shot versus v2
  zero-shot paired comparison (same build, same cap, same sealed order, same kernel session), each v2 arm versus the encoder, and the 0-empty
  result. What does NOT survive in that case: any comparison of v2 with a cap-600 arm as a measure of truncation alone, because the cap-600
  arms ran on 0.40.3 and 0.40.5 and the build also changed; those are reported as "truncation and build, not separable".
- Not changed: shots, prompt, scoring, sealed set, the encoder's numbers, Amendments 9 to 12.

### Amendment 14 (2026-10-18, S27 capacity): encoder-first routing, its capacity multiplier and its urgent-review recall criterion; registered before any router output and before any safety-stratum LLM row exists

Why: LLM extraction (pros, cons, topics, reply context) is the binding quota. `eval/capacity_model.json` (committed; per-model Groq pools of 200K tokens/day and 8K TPM, observed routing mix 40.6 percent gpt-oss-20b at 2,524.8 tokens per extraction and 59.4 percent gpt-oss-120b at 2,393.9) gives a real ceiling of 140.6 extractions a day (the 120b pool binds) = 4,217 a month at the hard limit, and half of that (2,109 a month) inside the 100K working budget used for eval runs. The pricing page quotas are Starter 5,000 and Growth 25,000 reviews a month (`docs/cost-model.md`). One customer at Starter needs 167 extractions a day; at Growth, 833. The encoder (Amendments 5 to 8, served as static-int8 ONNX) classifies a review with no LLM tokens. This amendment fixes, before anything is run, a routing rule that sends a review to full LLM extraction only when the encoder says it needs it, how the capacity gain is computed, and the safety criterion that decides whether the rule may ever be enabled. Nothing here enables anything: the feature flag stays OFF.

**Router R1 (fixed, the only rule eligible to be chosen).** Inputs are the SERVED decisions of the committed model (static-int8 ONNX, thresholds `app/core/classify/thresholds_run2_onnx_static_int8.json`, no re-fitting). A review is routed to LLM extraction iff ANY of: (a) `needs_action` label is `yes` or `abstained`; (b) `urgency` candidate_label is `high` (the per-class validation-fitted threshold decision, never argmax); (c) `urgency` label is `abstained`. Everything else is classified-only and is extracted on demand when the user opens the review. Rule R2 (R1 without clause (c)) is computed and REPORTED as a sensitivity only; it may not be selected because of its results.

**Capacity multiplier.** f = share of reviews routed by R1; o = share of non-routed reviews the user later opens (a product assumption, not measurable here: primary 0.10, sensitivities 0 and 0.30, BELIEVED). Multiplier M = 1 / (f + (1 - f) * o), on the assumption that extraction cost per review is unchanged (the encoder costs no LLM tokens). Extractions per day with the pipeline = 140.6 * M. f is measured on the 757 Amendment 5 sealed ids from the served-equivalent pred files (`served_per_item` parity is 0 mismatches of 3028, `reports/serving/v2_classify_onnx.md`). Caveat stated now: this sample is not a customer stream, so f transfers to a customer only to the extent that its needs_action and urgency mix resembles the corpus (BELIEVED).

**Expected values, written before f is computed.** Gold needs_action=yes is 17 percent of train, 18 percent of validation and 15.5 percent of the sealed test split (`reports/intent/labels/labels.json`, counts only); needs_action coverage at the conservative rule is about 0.89 and urgency coverage about 0.83 (`docs/STATUS.md`, run 2). Expected f(R1) on the sealed set: 0.25 to 0.45. Expected M at o = 0.10: 1.9 to 3.1 (at o = 0: 2.2 to 4.0). Expected consequence: at the hard limit Starter (167 a day) needs M >= 1.19 and is met; inside the 100K working budget (70.3 a day) Starter needs M >= 2.37 and is met only in the upper half of the range; Growth (833 a day) needs M >= 5.9 at the hard limit and is NOT reachable by this change alone. If the measured M falls outside the expected range that is reported as a miss, not tuned.

**Quality criteria (all on served decisions; a failure of C1 or C3 means the router must not be enabled and is reported as such).**
- C1 (primary, powered): on the safety stratum's sealed TEST split (Amendment 6; enriched, keyword-mined, silver labels; independent near-duplicate groups; NOT a base rate and never pooled with the Amendment 5 set), the share of gold `urgency=high` items routed by R1 (recall, group-weighted exactly as `engine/intent/safety_eval.py`): point estimate >= 0.95 AND Wilson 95 lower bound >= 0.90. This is where urgency-high has enough positives (the Amendment 5 sealed set has 4).
- C2 (reported, not a pass condition): the same routing recall on the Amendment 5 sealed set, where only 4 gold `urgency=high` items exist (insufficient data: counts and Wilson interval only).
- C3: routing recall of gold `needs_action=yes` on the Amendment 5 sealed test split (n about 112): point estimate >= 0.95 AND Wilson 95 lower bound >= 0.90.
- C4 (urgent reviews the LLM path would catch, "at matched precision"): the LLM path is gpt-oss-20b zero-shot with the Amendment 12 settings (reasoning effort low, 1,500-token cap, Ollama 0.40.6 pinned by Amendment 13) run on the same safety TEST items on Kaggle, zero extra cost. LLM-caught = gold `urgency=high` AND the LLM's urgency answer is `high`. Criterion: R1 routing recall on the LLM-caught set >= 0.97 (point estimate; Wilson interval reported). Because the LLM re-judges every routed review, a router's own precision only sets capacity, so precision is not a pass condition; to make the matched-precision reading explicit the report prints, side by side on the same items, the precision and recall of (i) the encoder's urgency=high decision at the committed serving threshold, (ii) the LLM's urgency=high answer, and (iii) R1 as a routing flag.
- Empty or unparseable LLM completions are reported beside every LLM number (count, and how many ended at the cap); an empty completion counts as not-caught for the LLM and the item is excluded from the LLM-caught set.
- Verdict rule: "may be enabled behind its flag" iff C1 and C3 and C4 hold; otherwise "must not be enabled" with the failing criterion named. In both cases the flag is OFF by default and nothing is deployed in this session.

**Not changed:** the encoder, its thresholds, the sealed sets, the labels, Amendments 5 to 13. Extraction quality for routed reviews is the existing LLM path and is not re-measured here.

### Amendment 15 (2026-10-19, S28): a fresh sealed set for the needs_action routing recall (Amendment 14, C3); registered before any fresh item is drawn, labelled or scored

Why: Amendment 14's criterion C3 (routing recall of gold `needs_action=yes`, point >= 0.95 and Wilson lower bound >= 0.90) was MISSED on the Amendment 5 sealed set: 106 of 111 routed = 0.955, lower bound 0.8989, by 0.0011. That verdict stands as recorded and is not revised here. Error analysis of the 5 misses (`reports/intent/fresh15/c3_miss_votes.json`): the four-judge panel did not split on them: 2 are unanimous 4-0 `yes` and 3 are 3-1 `yes`; across all 111 gold-yes items 90 are 4-0 and 21 (18.9 percent) are 3-1, so 3 of 5 (60 percent) is higher than the base rate but n = 5 cannot say more. The encoder was confident on all five (p(yes) 0.035 to 0.099). So they are not all ambiguous labels, and nothing about the router changes. n = 111 was simply too small to tell 0.955 from the 0.95 to 0.97 range at the required bound. This amendment adds a larger, fresh test with an absolute and a relative criterion. It does not change Amendment 14, the router R1, the thresholds or any earlier result, and it enables nothing.

**The fresh set (drawn once, sealed, never trained on).** About 2,000 items drawn with the Amendment 5 machinery (`engine/labelling/scale_sample.py` pools, cleaning and length limits; new module `engine/labelling/fresh_sample.py`, tag `fresh15`, seed 45) from the same licensed corpora, equal allocation over the three English strata (apparel, beauty, food). Zero overlap, enforced three ways before anything is labelled: (a) content hash not in any earlier manifest (the three pilots, the Amendment 5 scale manifest, the safety stratum split) or in `reports/labelling/ledger.json`; (b) no item is a near-duplicate of any earlier item of ANY split (exact Jaccard of 4-character shingle sets >= 0.9, the rule of `leakage_neardup`, against the private texts of every earlier set), nor of another fresh item; (c) after the draw the new hashes are added to the sealed list of `leakage_ledger` (all fresh items are test-only) and `leakage_ledger check` must report ok. The vernacular (Hinglish) pool was fully consumed by the Amendment 5 draw (113 items left, all drawn), so the fresh set is ENGLISH ONLY: it says nothing about Hinglish reviews, where the Amendment 5 sealed set has only 20 test items. Stated limit, not a reason to skip the test.

**Labels.** The four-judge panel of Amendment 5 (llama3.1:8b, qwen3:8b, gemma2:9b, mistral:7b; prompt `ri-judge-v3`; stage P of `engine/labelling/scale_plan.py`, every judge on every item) on Kaggle, no extra cost. Gold `needs_action` = a label at least 3 of the 4 judges agree on (the Amendment 5 consensus rule); items without it are excluded and counted. Silver, as before. The Ollama build is pinned (0.40.6, sha256-checked, Amendment 13) for this run; earlier panels used whatever build the kernel downloaded, so that difference is recorded, not corrected.

**Size.** Prior needs_action=yes share by stratum in the labelled scale sample: apparel 0.222, food 0.160, beauty 0.123 (equal allocation: mean 0.168), and 94.8 percent of items reach consensus. 2,000 items therefore give about 2,000 x 0.948 x 0.168 = 319 gold-yes items (expected range 270 to 360). Size rule, fixed now: it looks at LABELS ONLY, never at router output. If fewer than 250 gold-yes items exist after labelling, ONE top-up draw of 500 items (seed 46, same procedure and exclusions) is labelled and added; if still below 250 it is evaluated as it is and the interval says what it can.

**Systems scored on the same items.** (R) router R1 exactly as Amendment 14 (needs_action yes or abstained, urgency high-candidate or abstained) on the served static-int8 ONNX decisions with the committed thresholds, no re-fitting; (L) the all-LLM path. The production extraction schema has NO `needs_action` field (it returns urgency, cons, topics, feature requests), so the all-LLM comparator is the LLM answering the same question: gpt-oss-20b zero-shot with the `llm_moat.PROMPT` and the Amendment 12 settings (reasoning effort low, 1,500-token cap, Ollama 0.40.6; Amendment 13 showed the effort field is honoured), run on Kaggle on all fresh items. L "catches" an item iff its `needs_action` answer is `yes`; an empty or unparseable completion counts as NOT caught (counted and reported beside every number). The 120b model is unavailable (no key), so L is the 20b; this is the closest measurable stand-in for "the LLM path misses action items too" and is labelled as such. Whether the production extraction would surface a given action item through its urgency or cons fields is NOT measured (BELIEVED to be no better than L).

**Criteria (both reported; the verdict is computed, not written).** P = fresh gold-yes items.
- A, absolute: R's routing recall on P has Wilson 95 percent lower bound >= 0.90.
- B, relative, NON-INFERIORITY: with recall_R and recall_L on the same P (paired, item level; the fresh corpora carry no product ids), delta = recall_R minus recall_L; R is non-inferior to L iff the lower bound of the 95 percent paired-bootstrap interval of delta (2,000 resamples of items, seed 42) is greater than -0.05. Margin 0.05 absolute, fixed now: it is well inside the 18.9 percent of gold-yes items on which the panel itself split 3-1, so a smaller difference cannot be told from label noise, and it is the amount of recall this change may give up in exchange for a roughly 2.9 times capacity gain. Superiority is not claimed or required.
- Verdict rule: "criteria met" iff A and B both hold. "Criteria met" does NOT enable anything: it would only remove C3 as the reason Amendment 14 says the router must not be enabled. Enabling also needs the persistence of skipped reviews (a migration, gate 4) and the on-open extraction path, neither of which exists, plus a separate decision. If A or B fails that is reported as such and the router stays off.

**Expected values, written before any fresh item is drawn.** Gold-yes count 270 to 360. recall_R 0.93 to 0.97 (Amendment 14 observed 0.955). recall_L 0.80 to 0.95 (BELIEVED: the 20b zero-shot needs_action accuracy was 0.957 on 281 matched sealed items, but recall of the minority class is lower than accuracy). Share routed by R1 on the English fresh set 0.22 to 0.35 (0.275 observed on the Amendment 5 set, whose strata mix differs). If recall_R falls below 0.90 the miss is reported with the vote tallies of the misses, not tuned away.

**Report only (no pass condition):** recall_R on 4-0 versus 3-1 positives and by stratum; routed share and the capacity multiplier on the fresh set; routing recall of gold `urgency=high` (few positives); recall_L by confidence; per-judge agreement of the new panel against Amendment 5 (alpha, clear-consensus fraction) as a check that the fresh labels are comparable.

**Not changed:** Amendments 5 to 14, the encoder, its thresholds, rule R1, the sealed sets, every earlier result and verdict, the public site and pricing.
