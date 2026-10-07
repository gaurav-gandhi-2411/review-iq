# Competitor scan: code-mixed (Hinglish) review analytics (M2c)

Date: 2026-10-07. Method: public web pages only (WebSearch + WebFetch), no logins, no
bot-protection circumvention. Evidence labels: **[page]** = I fetched the vendor's own page
and read the claim there; **[snippet]** = claim seen only in a search-result summary of the
vendor page (the page itself was JS-rendered or not fetched); **[3rd-party]** = aggregator or
review site, not the vendor. "No mention" means no mention on the specific pages I read, NOT
that the capability is absent (rule 101a): I did not read every doc page of any vendor.

## Bottom line

The claim "nobody handles Hinglish" is **false as stated**. At least two vendors state some
support in their own words: Sprinklr (a "Hindi Romanized" language entry with recognition and
sentiment support) and Awshar AI / Mihup (explicit Hinglish claims). The defensible gap is
narrower: **review-level, product-aspect analytics for Indian e-commerce/D2C brands at
SMB-to-mid-market price**, with **measured, published accuracy on code-mixed text**. No
vendor I read publishes a Hinglish accuracy number (the only number I saw is Sprinklr's
generic "more than 80%" [snippet], not code-mixed specific). Whether our accuracy is actually
better is a separate question that this scan cannot answer.

## Per-competitor findings

| Vendor | Hinglish / code-mixed in their own words | Public price | Target customer | Does it close our gap? |
|---|---|---|---|---|
| **Yotpo** | **No mention.** Language-support doc lists "Hindi (hi)" for widget translations; nothing on Hinglish, transliteration or code-mixed [page: support.yotpo.com/docs/languages-yotpo-supports-for-widgets]. Hindi there is UI/translation, not analytics. | Reviews page shows Starter $89/mo, Pro $169/mo (up to 500 orders/mo), Premium/Enterprise custom, free tier up to 50 orders [page: yotpo.com/pricing]. A search summary showed different figures ($368 / $941), so prices vary by page/date; treat as indicative. | Shopify/DTC merchants, US-centric | No. AI summary / sentiment dashboard exists but nothing says it handles romanised Hindi. |
| **Bazaarvoice** | **No mention.** Sentiment Insights announced for French and German in 2021 [page]. Locale table lists India as `en_IN` only; moderation in 38 languages [snippet of developer docs]. | None public (contact sales); implementation fees $10k-$50k+ reported [3rd-party: Vendr via search summary] | Large retailers and brands | No (English/FR/DE for sentiment; nothing on Hindi analytics). |
| **Chattermill** | **No mention of Hindi or Hinglish** on the platform page. Claims "100+ languages with automated translation and transcription" and, on its blog, 99+ languages "natively without translation" [page + snippet]. Which languages is not stated. | None public. $55.7k-$101.3k/yr [3rd-party: Vendr via search summary] | Enterprise CX/VoC teams (Uber, Booking.com, HelloFresh, Tesco named) | Unproven. Generic multilingual claim; romanised code-mixed not addressed. Price puts it out of reach of Indian SMB. |
| **Sprinklr** | **Yes, partially.** Help-centre "Languages supported in Social Listening" lists "Hindi Romanized" with recognition = yes, sentiment = yes, word cloud/tokenization = no; also Bengali/Tamil/Urdu Romanized [snippet; the page is JS-rendered and my fetch returned an empty shell, so I did not read the table myself]. Sentiment is "100+ languages", "accuracy level of more than 80% in the languages it supports" [snippet]. Does not say "Hinglish" or mixed-in-sentence handling; romanised Hindi is a language label, not a code-mixing claim. | None public (enterprise contract) | Large enterprises, social listening and care | **Partly closes it** for social listening. Product-review aspect analytics for e-commerce is not what this page covers. |
| **Idiomatic (now Siena Insights)** | idiomatic.com redirects to siena.cx/insights [page]. Languages not stated on the page; aggregators list English only [3rd-party]. | Page links to pricing, not shown; aggregator "from $399/mo" [3rd-party] | Support/VoC teams (Upwork cited) | No evidence of Hindi/Hinglish. |
| **Locobuzz** (India) | Exists (Mumbai phone, Indian clients). Site says "200+ languages"; **no mention** of Hinglish or transliteration on home and pricing pages [page]. | Two plans (AI Pro, AI Pro Max), both "contact sales" [page]. Aggregator "from $399/mo" [3rd-party] | 350+ brands incl. Reliance Jio, IDFC First, Toyota; telecom/BFSI/retail | Unproven. Strong India incumbent in social listening and CX; Hinglish not claimed on pages read. Most realistic competitor for Indian brand buyers. |
| **Awshar AI** (Delishia Analytics, India) | **Yes, explicit:** "support for code-mixed languages such as Hinglish, Tanglish, and Manglish, using natural language processing models tailored to Indian linguistic patterns" [page: softwareadvice listing]. | From INR 2,999, flat-rate SaaS, India only [3rd-party listing] | Brands, agencies, BFSI, political groups, NGOs; social/brand-mention monitoring | **Yes on the claim.** Focus is social/brand monitoring, not product-review aspect analytics. No accuracy number seen. Small and young (2 reviews on the listing). |
| **Mihup** (Kolkata) | **Yes, explicit** but for **voice**: "code-mixed Hinglish", "Hinglish, Benglish, Tamilish", 120+ languages/dialects [snippet of company/press pages]. | Not public | Contact centres, auto OEMs (Tata Motors, SBI Life, IDFC First) | Different modality (calls). Not a review-text competitor; shows the Hinglish claim is common in Indian voice AI. |
| **Vue.ai** (Mad Street Den) | Exists. Retail AI: product tagging, catalogue, personalisation, visual search [3rd-party listings]. No review-analytics or Hinglish claim found. | Not public | Retailers (Nordstrom, Tata CLiQ) | Not a competitor on this axis; drop from the list. |
| **Haptik / Gupshup** | Not scanned in depth. Only found a Gupshup research dataset (Hinglish conversation summarisation) [snippet]. Both are conversational/messaging, not review analytics. | n/a | n/a | Out of category. |

## Where the gap is real vs not

- **Not real:** "No vendor supports romanised Hindi." Sprinklr lists it; Awshar and Mihup claim Hinglish outright. Do not say this in marketing.
- **Plausibly real (unverified):**
  1. Nobody I read publishes an accuracy figure on code-mixed text; claims are capability-level ("supports"). A published, honestly reported Hinglish eval (with n and a naive baseline) could differentiate, if our own numbers hold.
  2. Review-centric product aspects (delivery, packaging, size/fit, fake-review signal) for Indian e-commerce at an SMB price. Enterprise suites are quote-only and (where visible) five-to-six figures USD/yr; Yotpo/Bazaarvoice are review collection first and show no Hindi analytics.
  3. Chattermill/Locobuzz/Sprinklr may well handle Hinglish adequately via general LLMs; **I did not test any vendor's product**, so I cannot say they do it badly.
- **Unknown:** quality. Capability claims are not evidence of accuracy either way.

## Verified vs believed

- VERIFIED (page read): Yotpo no Hinglish mention, Hindi UI translation, prices above; Bazaarvoice FR/DE sentiment announcement; Chattermill "100+ languages", no Hindi mention; Locobuzz "200+ languages", no Hinglish mention, quote-only pricing; Siena redirect; Awshar listing text.
- VERIFIED via search summary only (JS page unreadable by my fetch): Sprinklr "Hindi Romanized" row and ">80%" statement; Bazaarvoice en_IN-only locale; Mihup Hinglish claims.
- BELIEVED / third-party: all enterprise price ranges (Vendr), "from $399" aggregator prices, Vue.ai description.
- NOT DONE: no vendor trial, demo or API test; Brandwatch, Qualtrics, Enterpret, Thematic and other VoC tools not scanned; Haptik/Gupshup not examined beyond one snippet.
- Cheap follow-up: run 50 labelled Hinglish reviews through a free trial (Awshar has one) to compare against our own numbers.
