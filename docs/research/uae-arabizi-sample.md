# UAE: Arabizi / code-mixed text in Noon and Amazon.ae feedback (M2d)

Date: 2026-10-07. Public sources only, no login, no bot-protection circumvention.

## What I could and could not fetch

- **Amazon.ae product reviews:** WebFetch of `amazon.ae/product-reviews/<ASIN>` returned HTTP 503 (Amazon also gates review pages behind sign-in). I did not retry or work around it. **No Amazon.ae product reviews sampled.**
- **Noon.com search/product pages:** fetch timed out (60 s); page is JS-rendered. **No Noon product reviews sampled.**
- **Google Play (Noon app page):** fetch returned no review text. Not used.
- **Substitute used, clearly NOT product reviews:** Apple App Store customer reviews for the UAE storefront via Apple's public iTunes RSS endpoint (`itunes.apple.com/ae/rss/customerreviews/...`, unauthenticated). Apps: Noon (id 1269038866) and Amazon (id 297606951). The feed returns the 500 most recent reviews per app; they reach back to 2026-02-21 (Noon) and 2026-01-08 (Amazon). These review the **shopping apps** (delivery, support, refunds), not products, and skew heavily to 1-star complaints. Not representative of product reviews on either site.

## Sample and method

- Pool: 1,000 reviews (500 Noon + 500 Amazon). Sample: 50 drawn with `random.seed(42)`, 25 per app (`random.sample`).
- Classes (I read each snippet; single rater, no second annotator, so no agreement figure): `EN` English only; `AR` Arabic script (incl. Gulf dialect); `AR+Latin` Arabic script with embedded Latin words; `ARABIZI` Arabic in Latin letters/digits; `HINGLISH` romanised Hindi mixed with English; `N/A` no linguistic content.
- Caveat: I recognise Arabizi by common markers (digits 3/7/5/2/9, words like wallah, shukran, yalla, kteer). I may miss dialect-specific romanised spellings.

## Result (n = 50)

| Class | Noon (25) | Amazon (25) | Total |
|---|---|---|---|
| EN | 22 | 19 | 41 |
| AR (Arabic script only) | 1 | 4 | 5 |
| AR+Latin (Arabic with embedded "Apple Pay", "vis") | 1 | 1 | 2 |
| N/A ("." and "0.0") | 1 | 1 | 2 |
| **ARABIZI** | 0 | 0 | **0** |
| **HINGLISH** | 0 | 0 | **0** |

- Arabizi: 0/50 (0%; 95% Wilson upper bound about 7%).
- Hinglish: 0/50 (0%; upper bound about 7%).
- Code-mixed by script (AR+Latin): 2/50 = 4%. Arabic script present in 7/50 = 14%.

### The 50 snippets and class

1 EN; 2 EN; 3 EN; 4 AR+Latin ("... Apple Pay او vis ..."); 5 EN; 6 N/A "."; 7 EN; 8 EN; 9 EN; 10 AR (account closed, Sharjah); 11 EN; 12 EN ("Yes"); 13 EN; 14 EN; 15 EN; 16 EN ("Sueprfast ... go noon"); 17 EN; 18 EN; 19 EN; 20 EN; 21 EN (non-native, "costumers"); 22 EN; 23 EN; 24 EN; 25 EN;
26 N/A "0.0"; 27 EN; 28 EN; 29 EN ("Using Amazon in India is worst experience"); 30 AR (worst delivery service); 31 EN (non-native, "two much coming lack"); 32 EN; 33 EN; 34 AR+Latin (payment cards, "Apple Pay"); 35 EN; 36 AR (Gulf dialect, "زعلانة كثير"); 37 EN; 38 EN; 39 AR (Ras Al Khaimah delivery); 40 EN; 41 EN; 42 EN; 43 EN; 44 EN; 45 EN; 46 EN; 47 AR (Gulf dialect, "مب كاملة"); 48 EN (informal, "mby"); 49 EN; 50 EN.

### Wider heuristic pass over all 1,000 (not hand-read)

Script counts: Noon 50 Arabic-only, 7 Arabic+Latin, 442 Latin-only; Amazon 66, 14, 420. An Arabizi word/digit-pattern scan produced 12 hits; reading them, 11 were false positives from a loose digit pattern and 1 was plausibly Arabizi-flavoured ("wallah" inside an English Noon review). Hinglish marker scan (3+ distinct common romanised-Hindi words): **0 of 1,000**. So: Arabic+Latin script mixing about 21/1,000 (2.1%), Arabizi about 1/1,000 (weak heuristic, may undercount), Hinglish 0/1,000.

## Interpretation

- In this substitute source Arabizi and Hinglish are both near zero. Users write English or Arabic script; mixing appears as Latin brand/feature words inside Arabic script, a different (script-mixed) problem from romanised code-mixing.
- Hinglish: absent in 1,000 app reviews even though South Asians are a large share of UAE residents. They seem to write plain English here. Romanised Hindi may exist in WhatsApp/social text; I have **no evidence either way for product-review pages**.
- Literature (not e-commerce specific): Arabizi is documented as widespread in Arab social media and chat, varying by country (the Alexandria dataset paper reports higher Latin-script mixing for Moroccan and Tunisian, low for most other groups). I found no published prevalence for Noon or Amazon.ae reviews.

## Is UAE a real adjacent market?

- **For "story":** 0 Arabizi and 0 Hinglish in n=50 (about 0 in 1,000). The romanised code-mixing need is not visible in this source.
- **Limits:** app-store reviews, one storefront, latest 500 per app, one rater. Says nothing about product reviews, Instagram/TikTok comments, WhatsApp commerce or call-centre text.
- **Verdict (uncertain):** UAE is **unproven**, and for the romanised code-mixing wedge it looks like a story. A UAE motion would more likely be Arabic-script dialect plus Arabic+English-brand-word analytics (7-14% Arabic script, 2-4% script-mixed here), which is a different NLP problem from Hinglish. Cheapest next test: a prospect-supplied export of Noon/Amazon.ae product reviews or Gulf social comments.

## Reproduce

Pattern: `https://itunes.apple.com/ae/rss/customerreviews/page=N/id=<id>/sortby=mostrecent/json`, N = 1..10, stdlib `urllib`, `random.seed(42)`, 25 per app. Raw pull is not committed (third-party review text, re-fetchable); the feed rolls forward, so a re-run returns different reviews.
