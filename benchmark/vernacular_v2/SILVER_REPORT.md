# review-iq Vernacular Benchmark — SILVER (multi-LLM consensus)

**SILVER BENCHMARK — labels are multi-LLM consensus, NOT human-verified ground truth. Numbers below measure AGREEMENT WITH CONSENSUS, NOT accuracy. DO NOT quote as accuracy externally.**

Labeler models: ['llama-3.3-70b-versatile', 'openai/gpt-oss-120b', 'qwen/qwen3-32b']
Consensus rule: unanimous=3/3, majority=2/3, split=no majority (no silver label assigned)

Predictions: 120/210 candidates have a successful v2.3 extraction (the rest hit real Groq daily-quota limits on the dedicated benchmark key — not a v2.3 correctness issue, see project memory for the incident).
Scoreable (silver label + successful prediction both present): 120

---

## Agreement with consensus (NOT accuracy)

### SENT

| Slice | n scored | n split (excluded) | Agreement w/ consensus |
|---|---|---|---|
| _all | 115 | 5 | 92.2% |
| en | 30 | 0 | 86.7% |
| hi-en | 49 | 3 | 93.9% |
| hi-en-missed | 36 | 2 | 94.4% |

### URG

| Slice | n scored | n split (excluded) | Agreement w/ consensus |
|---|---|---|---|
| _all | 115 | 5 | 73.0% |
| en | 28 | 2 | 85.7% |
| hi-en | 52 | 0 | 78.8% |
| hi-en-missed | 35 | 3 | 54.3% |

### LANG

| Slice | n scored | n split (excluded) | Agreement w/ consensus |
|---|---|---|---|
| _all | 116 | 4 | 68.1% |
| en | 29 | 1 | 100.0% |
| hi-en | 51 | 1 | 74.5% |
| hi-en-missed | 36 | 2 | 33.3% |

---

## Disagreement cross-cut — silver 'split' cases (no model majority)

Not resolved. These are the population most worth a human gold pass if GG
ever wants the provable version — surfacing where v2.3 lands when even the
3-model panel couldn't agree.

### SENT (5 split cases)

| ID | Slice | Text | Model votes | v2.3 predicted |
|---|---|---|---|---|
| flipkart-199635 | hi-en-missed | painsa wasool | llama-3.3-70b-versatile=None, gpt-oss-120b=positive, qwen3-32b=negative | negative |
| flipkart-236064 | hi-en | amazing night lamp but it discharge very quickly that is so annoying coz you have to charge it every | llama-3.3-70b-versatile=None, gpt-oss-120b=negative, qwen3-32b=neutral | negative |
| flipkart-217452 | hi-en-missed | how is it look but not that is so ghatiya | llama-3.3-70b-versatile=None, gpt-oss-120b=negative, qwen3-32b=positive | negative |
| flipkart-241948 | hi-en | built quality achaa nahi good service from flipkart best demo from ifb | llama-3.3-70b-versatile=None, gpt-oss-120b=neutral, qwen3-32b=negative | neutral |
| flipkart-008846 | hi-en | Good product but mop ke nichhe scratch bahut hain | llama-3.3-70b-versatile=None, gpt-oss-120b=neutral, qwen3-32b=negative | neutral |

### URG (5 split cases)

| ID | Slice | Text | Model votes | v2.3 predicted |
|---|---|---|---|---|
| flipkart-199635 | hi-en-missed | painsa wasool | llama-3.3-70b-versatile=None, gpt-oss-120b=low, qwen3-32b=medium | high |
| flipkart-192505 | en | autocut button stopped working within 2 weeks of use now the device has no local control as it heats | llama-3.3-70b-versatile=None, gpt-oss-120b=medium, qwen3-32b=high | high |
| flipkart-217452 | hi-en-missed | how is it look but not that is so ghatiya | llama-3.3-70b-versatile=None, gpt-oss-120b=medium, qwen3-32b=low | low |
| flipkart-145676 | hi-en-missed | Faltu | llama-3.3-70b-versatile=None, gpt-oss-120b=low, qwen3-32b=medium | low |
| flipkart-242529 | en | shaking | llama-3.3-70b-versatile=None, gpt-oss-120b=low, qwen3-32b=medium | medium |

### LANG (4 split cases)

| ID | Slice | Text | Model votes | v2.3 predicted |
|---|---|---|---|---|
| flipkart-040694 | hi-en-missed | Very good product... Fully digital.. I am using with amaron 200ah battery... And its giving me a won | llama-3.3-70b-versatile=None, gpt-oss-120b=en, qwen3-32b=hi-en | hi-en |
| flipkart-078108 | hi-en | Sound quality amazing bass is so good.Design are suparb.Big boom baam speaker.Mini dj sound ☺️Full P | llama-3.3-70b-versatile=None, gpt-oss-120b=hi-en, qwen3-32b=en | hi-en |
| flipkart-127844 | en | Best ?????? clock with utsav deal price Thanks flipkart | llama-3.3-70b-versatile=None, gpt-oss-120b=en, qwen3-32b=hi-en | en |
| flipkart-168504 | hi-en-missed | Totally faltu ptoduct.waste for money....trust has grown from the Flipkart authority.... | llama-3.3-70b-versatile=None, gpt-oss-120b=en, qwen3-32b=hi-en | en |

