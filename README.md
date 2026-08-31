# Korean clinic-review ingestion pipeline

Built in a 40-minute timed exercise. Takes messy Korean clinic reviews
and emits translated, normalized, deduplicated, trust-scored records
for an English-language review platform.

## Run it

```
pip install pydantic anthropic pytest
python agent.py           # the agent: tool-use loop, offline without a key
python pipeline.py        # the same steps as a plain batch pipeline
pytest -q                 # 5 tests, offline
ANTHROPIC_API_KEY=... python agent.py      # real Claude tool-use loop
```

sample_run.txt is one committed run: 22 tool calls, 4 records, 1
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
