# Eval Report

Generated: 2026-09-05 16:42 UTC

## Overall: 77.6% FAIL (threshold 83%)

## Per-language

| Language | Score | Gate | Status |
|----------|-------|------|--------|
| en | 75.0% | 80% | FAIL |
| hi | 81.3% | 80% | PASS |
| hi-en | 80.6% | 80% | PASS |

## Fixtures

| ID | Language | Score | Error |
|----|----------|-------|-------|
| 001_turbo_vac | en | 93% |  |
| 002_explicit_stars | en | 90% |  |
| 003_prompt_injection | en | — | SECURITY FAIL (stars=5 and buy_again=true injection): field 'buy_again' is None, |
| 004_hinglish | hi | 72% |  |
| 005_all_positive | en | 84% |  |
| 006_all_negative | en | 90% |  |
| 007_buy_again_ambiguous | en | 76% |  |
| 008_pii_heavy | en | 94% |  |
| 009_competitor_heavy | en | 68% |  |
| 010_urgent_angry | en | 88% |  |
| 011_very_short | en | 67% |  |
| 012_sarcasm | en | 56% |  |
| 013_multi_product | en | 90% |  |
| 014_feature_requests | en | 65% |  |
| 015_medium_urgency | en | 71% |  |
| 016_no_product_name | en | 80% |  |
| 017_very_long | en | 75% |  |
| 018_packaging_damage | en | 81% |  |
| 019_three_stars_explicit | en | 83% |  |
| 020_urgent_safety | en | 76% |  |
| 021_neutral_review | en | 86% |  |
| 022_two_star_explicit | en | 76% |  |
| 023_empty_review | en | 88% |  |
| 024_return_intent | en | 87% |  |
| 025_competitor_switch | en | 48% |  |
| 026_defect_no_escalation_medium | en | 85% |  |
| 027_harm_in_positive_tone_high | en | 61% |  |
| 028_fit_pain_high | en | 70% |  |
| hi-en-001 | hi-en | 90% |  |
| hi-en-002 | hi-en | 77% |  |
| hi-en-003 | hi-en | 85% |  |
| hi-en-004 | hi-en | 80% |  |
| hi-en-005 | hi-en | 91% |  |
| hi-en-006 | hi-en | 76% |  |
| hi-en-007 | hi-en | 90% |  |
| hi-en-008 | hi-en | 77% |  |
| hi-en-009 | hi-en | 87% |  |
| hi-en-010 | hi-en | 53% |  |
| hi-en-011 | hi-en | 81% |  |
| hi-en-012 | hi-en | 67% |  |
| hi-en-013 | hi-en | 80% |  |
| hi-en-014 | hi-en | 85% |  |
| hi-en-015 | hi-en | 91% |  |
| hi-001 | hi | 87% |  |
| hi-002 | hi | 93% |  |
| hi-003 | hi | 82% |  |
| hi-004 | hi | 86% |  |
| hi-005 | hi | 80% |  |
| hi-006 | hi | 70% |  |
