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
   org, within the 100K tokens per model per UTC day ceiling, so the comparison is spread over days), and the
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
