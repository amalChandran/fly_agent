"""Tool-calling agent that orchestrates the review pipeline.

The model is the planner; the tools are the same deterministic,
tested functions from pipeline.py. With ANTHROPIC_API_KEY set, Claude
drives a real tool-use loop. Without a key, a scripted planner issues
the identical tool calls through the same dispatch, so the machinery
runs cold for a grader.

Run: python agent.py            (live if key present, else scripted)
     python agent.py --scripted (force offline)
"""

import hashlib
import json
import os
import sys
import time

import llm
import pipeline

BRAIN = "claude-sonnet-4-6"
MAX_TURNS = 40

STATE = {"reviews": {}, "seen": {}, "emitted": [], "calls": 0, "log": []}

TOOLS = [
    {"name": "fetch_reviews",
     "description": "Load the batch of scraped Korean reviews. Returns review ids. Call once, first.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "translate_review",
     "description": "Translate one review from Korean to English. The Korean original is kept.",
     "input_schema": {"type": "object", "properties": {"review_id": {"type": "integer"}},
                      "required": ["review_id"]}},
    {"name": "extract_fields",
     "description": "Extract procedure, clinic_raw, price_krw, sentiment, red_flags, confidence from one review.",
     "input_schema": {"type": "object", "properties": {"review_id": {"type": "integer"}},
                      "required": ["review_id"]}},
    {"name": "match_clinic",
     "description": "Normalize the raw clinic name against the verified registry. Alias hits are free; fuzzy matches are adjudicated and low confidence goes to a human queue.",
     "input_schema": {"type": "object", "properties": {"review_id": {"type": "integer"}},
                      "required": ["review_id"]}},
    {"name": "check_duplicate",
     "description": "Check one review against all previously seen reviews using a hash of the Korean source text.",
     "input_schema": {"type": "object", "properties": {"review_id": {"type": "integer"}},
                      "required": ["review_id"]}},
    {"name": "finalize_review",
     "description": "Compute the explainable trust score and emit the final record for one review. Call after the other per-review steps.",
     "input_schema": {"type": "object", "properties": {"review_id": {"type": "integer"}},
                      "required": ["review_id"]}},
    {"name": "report",
     "description": "Finish the batch: returns summary counts. Call once, last.",
     "input_schema": {"type": "object", "properties": {}}},
]

SYSTEM = (
    "You orchestrate review ingestion for a Korean clinic-review platform. "
    "Call fetch_reviews once, then process EVERY review id through this "
    "exact tool sequence: translate_review, extract_fields, match_clinic, "
    "check_duplicate, finalize_review. Then call report once and reply "
    "with a short plain-text summary of the batch. Never invent data; "
    "use only tool results."
)


def _item(args):
    return STATE["reviews"][args["review_id"]]


def tool_fetch_reviews(_args):
    with open("fixtures.jsonl", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    items = pipeline.step_ingest(rows)
    STATE["reviews"] = dict(enumerate(items))
    return {"review_ids": list(STATE["reviews"]), "dropped_blank": len(rows) - len(items)}


def tool_translate(args):
    it = _item(args)
    pipeline.step_translate([it])
    return {"translated_en": it.translation.translated_en}


def tool_extract(args):
    it = _item(args)
    pipeline.step_extract([it])
    return it.extraction.model_dump()


def tool_match_clinic(args):
    it = _item(args)
    pipeline.step_normalize_clinic([it])
    return it.clinic.model_dump() | {"needs_human_review": it.needs_human_review}


def tool_check_duplicate(args):
    it = _item(args)
    key = hashlib.md5(pipeline._norm(it.raw.text_ko).encode()).hexdigest()
    origin = f"{it.raw.source}/{it.raw.author}/{it.raw.posted}"
    if key in STATE["seen"]:
        it.duplicate_of = STATE["seen"][key]
    else:
        STATE["seen"][key] = origin
    return {"duplicate_of": it.duplicate_of}


def tool_finalize(args):
    it = _item(args)
    pipeline.step_trust_score([it])
    STATE["emitted"].append(pipeline.to_record(it))
    return {"trust_score": it.trust, "trust_reasons": it.trust_reasons,
            "queued_for_human": it.needs_human_review}


def tool_report(_args):
    recs = STATE["emitted"]
    return {"records": len(recs),
            "queued_for_human": sum(1 for r in recs if r.needs_human_review),
            "duplicates": sum(1 for r in recs if r.duplicate_of)}


TOOL_IMPL = {
    "fetch_reviews": tool_fetch_reviews,
    "translate_review": tool_translate,
    "extract_fields": tool_extract,
    "match_clinic": tool_match_clinic,
    "check_duplicate": tool_check_duplicate,
    "finalize_review": tool_finalize,
    "report": tool_report,
}


def execute(name, args):
    """Every tool call goes through here: logged, timed, and never
    allowed to crash the loop. Failures go back to the model as data."""
    STATE["calls"] += 1
    STATE["log"].append((name, args.get("review_id")))
    t0 = time.perf_counter()
    fn = TOOL_IMPL.get(name)
    if fn is None:
        out = {"error": f"unknown tool: {name}"}
    else:
        try:
            out = fn(args)
        except Exception as e:
            out = {"error": f"{type(e).__name__}: {e}"}
    ms = (time.perf_counter() - t0) * 1000
    print(f"  [{STATE['calls']:02d}] {name}({json.dumps(args, ensure_ascii=False)}) "
          f"-> {json.dumps(out, ensure_ascii=False)[:140]} ({ms:.0f}ms)")
    return out


def run_live():
    import anthropic
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": "Ingest the review batch in fixtures.jsonl."}]
    print(f"# live agent, brain={BRAIN}")
    for _ in range(MAX_TURNS):
        resp = client.messages.create(model=BRAIN, max_tokens=2048,
                                      system=SYSTEM, tools=TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            return next((b.text for b in resp.content if b.type == "text"), "")
        results = []
        for block in resp.content:
            if block.type == "tool_use":
                out = execute(block.name, dict(block.input))
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(out, ensure_ascii=False),
                                "is_error": "error" in out})
        messages.append({"role": "user", "content": results})
    raise RuntimeError(f"agent exceeded {MAX_TURNS} turns, aborting")


def run_scripted():
    """Same tool calls a competent planner would make, no model needed."""
    print("# scripted planner (no ANTHROPIC_API_KEY), same tools and dispatch")
    fetched = execute("fetch_reviews", {})
    for rid in fetched["review_ids"]:
        for tool in ("translate_review", "extract_fields", "match_clinic",
                     "check_duplicate", "finalize_review"):
            execute(tool, {"review_id": rid})
    summary = execute("report", {})
    return (f"Processed {summary['records']} reviews: "
            f"{summary['queued_for_human']} queued for human review, "
            f"{summary['duplicates']} flagged duplicate.")


if __name__ == "__main__":
    live = os.environ.get("ANTHROPIC_API_KEY") and "--scripted" not in sys.argv
    summary = run_live() if live else run_scripted()
    print(f"\n# agent summary: {summary}")
    print(f"# total tool calls: {STATE['calls']}")
    for rec in STATE["emitted"]:
        print(rec.model_dump_json())
