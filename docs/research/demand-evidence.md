# Demand evidence (M2b)

Date: 2026-10-07. Method: WebSearch + WebFetch, public pages only, no logins, no outreach.

## Bottom line

The assumed pain was NOT found in practitioners' own words. In this research pass I found zero
first-person quotes from an Indian seller, brand or agency saying they cannot analyse Hinglish or
code-mixed reviews, that they read reviews manually at scale, or that they cannot keep up with
replying. What I did find:

1. Fake/hostile reviews: real, repeated seller complaints, but from Amazon UK/US/EU seller forums
   (not India-specific), plus Indian press on the fake-review trade.
2. Hinglish reviews exist and break English-only tools: stated by academic papers and vendor/search
   summaries, not by buyers of the product.
3. Review/rating monitoring appears as a duty in agency pages and job-post snippets, which shows
   the task is done by humans, but not that it hurts.

Absence here is weak evidence: Reddit was inaccessible (below) and LinkedIn/Quora returned nothing
usable. It is not evidence that the pain is absent.

## What could not be searched (honest coverage)

- Reddit (r/FulfillmentByAmazon, r/IndianEcommerce, r/indianstartups, Flipkart sellers): BLOCKED.
  WebSearch rejected reddit.com as an allowed domain ("not accessible to our user agent") and
  WebFetch refused www.reddit.com. Zero Reddit threads read. This is the single biggest gap.
- LinkedIn posts: searched via open web queries; no LinkedIn post surfaced. Zero read.
- Quora: searched via open web queries; no Quora thread surfaced. Zero read.
- Agency job postings: two Weekday job pages fetched showed only title/location (no description);
  responsibilities below come from a search-result snippet only.

## Evidence log

Label key: VERIFIED = I fetched the page and the text came back from that page (via the fetch
tool's summary; re-check wording before external use). SNIPPET = seen only in a search-result
summary. BELIEVED = my inference, not seen in a source.

### A. Fake / hostile reviews (pain: "fake reviews")

| # | Source | Quote | Who/where | Label |
|---|--------|-------|-----------|-------|
| A1 | https://sellercentral-europe.amazon.com/seller-forums/discussions/t/110fd048-8458-4cb4-9478-ae101fb75c5b | "My product reviews have always been very good. Last week, I suddenly received 4 negative reviews in a row. My review dropped from 4.5 to 3.6." | Amazon UK seller forum poster. NOT India. | VERIFIED |
| A2 | same thread | Other sellers said Amazon's investigation "can take months" and platforms rarely remove reviews (paraphrase from fetch summary, not a verbatim quote) | Amazon UK forum | VERIFIED (paraphrase) |
| A3 | https://inc42.com/?p=185691 (Inc42, 26 Dec 2019, Shanthi S) | "It is 15 euros (around INR 1200) per review but much cheaper when you buy a package... It's not really legal but it's not really illegal. So it's like in a grey zone" | An AMZTigers (review broker) employee quoted from a Daily Mail investigation. Supply side, not a seller complaint. | VERIFIED |
| A4 | https://www.desidime.com/discussions/seller-harassing-me-after-posting-a-negative-review-and-returning-product | "I actually got a call from Rahul Saksule, who said he was the CEO of Mobicore. He tried to convince me to change my review to 5 stars." | Indian BUYER on DesiDime (Amazon seller pressured him). Buyer-side. | VERIFIED |
| A5 | https://www.marketplacepulse.com/articles/100-million-seller-reviews-on-amazon-marketplace (13 Dec 2017) | "In India 13% of all reviews were negative." Highest of the marketplaces in that study; 2017 data, method not detailed. | Marketplace Pulse analysis of seller (not product) reviews | VERIFIED |
| A6 | Amazon Seller Central forum threads (titles only: "Fake Reviews From A Competitor", "Malicious attacks from competitors", "Competitor Engaging in Fake Reviews..." etc., sellercentral-europe.amazon.com / sellercentral.amazon.com) | Not individually fetched | EU/US forums | SNIPPET |

Count in this section: 4 verbatim quotes, all on pages fetched; 1 from a seller (A1, non-India), 1 broker
(A3), 1 buyer (A4), 1 statistic (A5).

### B. Hinglish / code-mixed reviews (pain: "cannot analyse at scale")

| # | Source | Statement | Label |
|---|--------|-----------|-------|
| B1 | Search summary of "Sentiment Analysis of Products' Reviews Containing English and Hindi Texts" (dl.ifip.org/hal-01448058v1, IFIP) | Reviews by Indian buyers are mainly English but contain Hindi in Roman script ("bahut achha", "bakbas", "pesa wasool"); earlier work "neglect these texts as they are mainly developed for English texts only." Fetch of the paper itself failed (Anubis access-denied page). | SNIPPET (academic, not a buyer) |
| B2 | https://www.actowizsolutions.com/ecommerce-qcommerce-review-intelligence-report.php | Vendor marketing for review-data scraping on Flipkart/Nykaa/Purplle/BigBasket. Fetch summary: no mention of Hindi, Hinglish or regional languages, no verbatim brand quotes. | VERIFIED (negative: vendor copy ignores the language issue) |
| B3 | https://echai.ventures/d2c/d2c-category-research/how-indian-d2c-brands-actually-do-product-research | Says Indian D2C founders do "extensive review/feedback mining" (fetch-summary phrasing). No quote on language or manual reading. | VERIFIED (weak support for "reviews are mined", none for Hinglish) |

First-person practitioner quotes on Hinglish: 0.

### C. Manual review reading / monitoring and responding

| # | Source | Statement | Label |
|---|--------|-----------|-------|
| C1 | https://swcybernetics.in/services/flipkart-account-management | Agency lists "Rating & Review Management" as a service | VERIFIED |
| C2 | https://globalwebsters.com/services/marketplace-management | "Daily rating monitoring with fast response to negative feedback" and "Automated post-delivery review flows via WhatsApp and email" | VERIFIED |
| C3 | https://www.digitaldawn.in/?p=15049 | "We manage the entire review and feedback process on your behalf through a dedicated dashboard built for this purpose." | VERIFIED |
| C4 | Weekday job listings (soviv "Marketplace Manager - Amazon, Flipkart, Myntra", JD Fresh "E-Commerce Executive - Flipkart & Firstcry") | Search snippet says roles include "monitoring account health, reviews/ratings, and return trends" and "tracking seller scorecards, ratings". The fetched job pages showed no description. | SNIPPET |
| C5 | Eshopbox blog (eshopbox.com/blog/marketplace-management-services) | Lists "feedback management" among marketplace-management services | VERIFIED |

These show review/rating watching is a staffed, sold task. They do not show anyone saying it is
painful, slow, or unserved. Where it is sold (C2, C3), it is review solicitation and star-rating
monitoring, which is a different job from analysing review text.

## Frequency of each assumed pain, in sources actually read

| Assumed pain | Practitioner quotes found | Notes |
|---|---|---|
| Cannot analyse Hinglish/code-mixed reviews at scale | 0 | Only academic/search-summary claims (B1). Vendor copy (B2) silent. |
| Manual review reading | 0 first-person | Job/agency pages (C1-C5) show humans do monitoring; no complaint. |
| Fake reviews | 1 seller quote (A1), non-India | Strong EU/US forum signal (A6, snippet); Indian signal is press (A3) and a buyer (A4). |
| Responding to reviews | 0 first-person | C2 shows response to negative feedback is sold as a service. |

## Totals

- Distinct sources opened/fetched with content returned: about 14 (Inc42, DesiDime, Marketplace
  Pulse, Amazon UK forum thread, Actowiz, echai.ventures, SW Cybernetics, Global Websters, Digital
  Dawn, Eshopbox blog, TechEnclave thread, plus agency pages in agency-prospects.md). Fetch failures:
  Reddit (refused), Quartz (403), IFIP paper (access-denied), HuffPost India (redirect not followed),
  two job pages (empty).
- Searches run for this task (M2b): about 14 queries across web, plus 4 attempts restricted to
  reddit.com/quora.com/linkedin.com that errored out.
- Verbatim quotes: 4 in section A (1 seller non-India, 1 broker, 1 buyer, 1 statistic) plus 3 agency
  marketing lines (C2 x2, C3). 0 on Hinglish. 0 first-person from an Indian seller/brand/agency.
- Labels: VERIFIED 12 items (A1-A5, B2-B3, C1-C3, C5, A2 paraphrase); SNIPPET 3 (A6, B1, C4); BELIEVED
  items are confined to the inference below.

## Inference (BELIEVED, not evidenced)

It is plausible that Hinglish handling matters for aspect/defect extraction, since reviews are
Roman-script Hindi (B1) and English-only tools ignore it. That is a reasoned guess, supported only
by an academic abstract summary. A real test of demand needs first-hand sources this pass could not
reach: Reddit threads read through a browser by a human, LinkedIn posts, and conversations. Suggested
next steps (no action taken): GG reads r/FulfillmentByAmazon, r/IndianEcommerce and r/indianstartups
manually for the four pains and appends quotes here; treat a zero-hit result as a real negative.
