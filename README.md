# Korean clinic-review ingestion agent

> The model plans. Tools act. State never rides the context window.

Messy Korean clinic reviews in; translated, clinic-normalized,
deduplicated, trust-scored records out. A Claude tool-use loop drives
seven deterministic, tested tools. Built in a 40-minute timed exercise.

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

sample_run.txt is the full committed run: 22 tool calls, 4 records, 1
duplicate flagged, 1 queued for human review.

## The agent

agent.py is a Claude tool-use loop. The model plans; seven tools act,
and each tool is one of the deterministic, tested functions below.
Full review state lives server-side keyed by review_id, so the model
only ever sees compact JSON tool results, not full payloads. Without an
API key, a scripted planner issues the identical calls through the same
dispatch, so the machinery runs cold for anyone cloning this.

## The steps

| step | tool | what it does | what feeds forward |
|---|---|---|---|
| ingest | pydantic | validate scraped rows, drop empties | RawReview |
| translate | LLM (cheap tier) | Korean to English, original kept | Translation |
| extract | LLM (cheap tier) + schema | procedure, clinic, price, sentiment, red flags | Extraction |
| normalize clinic | alias table, then LLM (judge tier) | raw name to canonical registry entry | ClinicMatch |
| dedup | md5 on normalized Korean text | cross-posts flagged, canonical kept | duplicate_of |
| trust score | plain rules | 0-100 with named reasons | Record |

## Design choices

- LLM only where judgment lives. Translation, extraction, and entity
  adjudication are model calls. Ingest, dedup, scoring, and routing are
  deterministic code: same input, same path, debuggable.
- Dedup hashes the Korean source, not the translation. Cross-posted
  reviews mutate less before translation than after.
- Exact alias hits never call a model. Only fuzzy matches pay for the
  judge tier, and anything under 0.80 confidence goes to a human queue
  instead of into the index. Wrong clinic attribution on medical
  content is worse than a review queue.
- Trust score is deterministic and every point has a named reason. An
  opaque score is not defensible to a clinic or a patient.
- Two model tiers: cheap for bulk translate/extract, strong only for
  low-volume entity adjudication.
- Mock seam in llm.py so the whole thing runs cold without a key.

## Testing the tests

A test that cannot fail is noise. Before submitting, six deliberate
breaks went into the code one at a time; each one must turn the suite
red or the covering test gets fixed.

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
