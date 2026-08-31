"""LLM seam: real Anthropic API when ANTHROPIC_API_KEY is set, a
deterministic mock otherwise, so the pipeline runs cold with no key.

Two model tiers on purpose: translation and bulk extraction are cheap
and high-volume, entity adjudication is low-volume judgment work.
"""

import json
import os
import time

MODEL_BULK = "claude-haiku-4-5-20251001"
MODEL_JUDGE = "claude-fable-5"


def call(system: str, user: str, model: str = MODEL_BULK, retries: int = 3) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _mock(system, user)
    import anthropic
    client = anthropic.Anthropic()
    for attempt in range(retries):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=1024,
                temperature=0,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return resp.content[0].text
        except anthropic.APIStatusError:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def call_json(system: str, user: str, model: str = MODEL_BULK) -> dict:
    """JSON-only contract with one repair retry. Callers validate the
    result against a pydantic schema; validation failure routes the item
    to human review, never silently downstream."""
    out = call(system + "\nReply with a single JSON object, nothing else.", user, model)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        repaired = call("Fix this into valid JSON. Reply with JSON only.", out, model)
        return json.loads(repaired)


def _mock(system: str, user: str) -> str:
    """Deterministic offline stand-in. Keys off the task named in the
    system prompt and visible tokens in the input, so the downstream
    deterministic steps (alias table, dedup, trust) do real work."""
    task = system.lower()
    if "translate" in task:
        return json.dumps({"translated_en": "[EN mock] " + user[:60], "source_lang": "ko"})
    if "adjudicate" in task:
        payload = json.loads(user)
        candidates = payload.get("candidates") or ["UNMATCHED"]
        return json.dumps({"canonical": candidates[0], "confidence": 0.62})
    if "extract" in task:
        flags = ["sponsored_language"] if "협찬" in user else []
        if "원진" in user or "wonjin" in user.lower():
            clinic, proc = "Wonjin Plastic Surgery", "double eyelid"
        elif "아이디" in user or "ID병원" in user:
            clinic, proc = "아이디병원", "rhinoplasty"
        else:
            clinic, proc = "김피부과", "laser toning"
        return json.dumps({
            "procedure": proc, "clinic_raw": clinic,
            "price_krw": 3500000 if "350" in user else None,
            "sentiment": "negative" if flags else "positive",
            "red_flags": flags, "confidence": 0.55 if flags else 0.9,
        })
    return json.dumps({"result": "mock", "confidence": 0.5})
