# Korean clinic-review ingestion agent

> The model plans. Tools act. State never rides the context window.

Messy Korean clinic reviews in; translated, clinic-normalized,
deduplicated, trust-scored records out. A Claude tool-use loop drives
seven deterministic, tested tools. LLM calls exist only where judgment
lives; everything that can be boring code is boring code.

**7 tools · 22-call committed run · 15 offline tests · 6/6 mutants
caught · 3 deps · built solo in a 40-minute timed exercise**

```
fixtures.jsonl ─▶ fetch ─▶ translate ─▶ extract ─▶ match_clinic ─▶ dedup ─▶ trust ─▶ records
                           LLM, cheap   LLM, cheap  alias ▸ LLM judge  md5(ko)  plain rules
                                        anything under 0.80 confidence ─▶ human review queue
```

## Run it

```
pip install pydantic anthropic pytest
make run       # the agent; offline planner without a key, Claude loop with one
make test      # 15 tests, offline
make mutants   # break the code six ways, the suite must go red each time
```

## What a run looks like

```
  [04] match_clinic({"review_id": 0}) -> {"canonical": "ID Hospital", "confidence": 1.0, "via": "alias_table", ...} (0ms)
  [10] check_duplicate({"review_id": 1}) -> {"duplicate_of": "naver_cafe/user_a/2026-07-02"} (0ms)
  [14] match_clinic({"review_id": 2}) -> {"canonical": "Wonjin Plastic Surgery Clinic", "confidence": 0.62, "via": "llm_adjudication", "needs_human_review": true} (0ms)
  [21] finalize_review({"review_id": 3}) -> {"trust_score": 40, "trust_reasons": [..., "-25 red flags: sponsored_language"], ...} (0ms)
  [22] report({}) -> {"records": 4, "queued_for_human": 1, "duplicates": 1}

# agent summary: Processed 4 reviews: 1 queued for human review, 1 flagged duplicate.
```

sample_run.txt is the full committed run.

## How the loop is built

- **The model plans, tools act.** agent.py is a Claude tool-use loop
  (Sonnet brain). Each of the seven tools is a deterministic, tested
  function. The model never touches raw state.
- **State lives host-side**, keyed by review_id. The planner sees only
  compact JSON tool results, so context stays flat no matter how big
  the batch gets.
- **Failure is data, not death.** Unknown tools and tool exceptions
  come back as `is_error` results the model can react to. The loop is
  capped at 40 turns. Every call is logged and timed.
- **Runs cold.** No API key: a scripted planner issues the identical
  calls through the same dispatch. Same machinery, no model bill.

## The steps

| tool | runs on | what it does | feeds forward |
|---|---|---|---|
| fetch_reviews | pydantic | validate scraped rows, drop empties | review ids |
| translate_review | LLM, cheap tier | Korean to English, original kept | Translation |
| extract_fields | LLM, cheap tier + schema | procedure, clinic, price, sentiment, red flags | Extraction |
| match_clinic | alias table, then LLM judge | raw name to canonical registry entry | ClinicMatch |
| check_duplicate | md5 on normalized Korean | cross-posts flagged, canonical kept | duplicate_of |
| finalize_review | plain rules | 0-100 trust score with named reasons | Record |
| report | counters | batch summary back to the planner | done |

## Design choices

- **Dedup hashes the Korean source, not the translation.** Cross-posted
  reviews mutate less before translation than after.
- **Exact alias hits never call a model.** Only fuzzy matches pay for
  the judge tier, and anything under 0.80 confidence goes to a human
  queue instead of into the index. Wrong clinic attribution on medical
  content is worse than a review queue.
- **Trust scores are deterministic and every point has a named
  reason.** Sponsored language -25, duplicate -30, verified-registry
  clinic +15. An opaque score is not defensible to a clinic or a
  patient.
- **Two model tiers.** Cheap for bulk translate and extract, strong
  only for low-volume entity adjudication.
- **Schema failures route to humans**, never silently downstream.

## Testing the tests

A test that cannot fail is noise. mutcheck.py breaks the code six ways,
one at a time; each break must turn the suite red, and the run exits 1
if any mutant survives.

| mutation | result |
|---|---|
| remove trust-score clamp | caught |
| loosen human-queue threshold 0.80 to 0.30 | caught |
| kill the alias fast path | caught |
| dedup on translation instead of Korean source | caught |
| weaken red-flag penalty | caught |
| unknown tool crashes the loop | caught |

The dedup mutation was first caught only by an incidental crash, not by
an assertion. That is luck, not coverage, so
test_dedup_keys_on_korean_not_translation now exists: two different
reviews engineered to collide after translation but not before it.

## Cut for time, next in line

1. Near-dup detection beyond exact-normalized hash (simhash over
   Korean character n-grams). The seam is `step_dedup`.
2. Sponsored-review classifier beyond keyword red flags. K-beauty
   review inventory has a heavy incentivized-post problem; this is a
   labeled-fixtures eval before it is a model call.
3. Price banding per procedure (packages in KRW vary wildly; outliers
   are a trust signal).
4. Real scrapers. Fixtures stand in for source connectors on purpose:
   the interesting engineering is downstream of fetch.
