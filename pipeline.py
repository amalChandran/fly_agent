"""Korean clinic-review ingestion pipeline.

ingest -> translate -> extract -> normalize clinic -> dedup -> trust score -> emit

Design rule: LLM calls only where judgment lives (translation nuance,
field extraction, entity adjudication). Control flow, dedup keys, alias
hits, and scoring are deterministic code so the same input always takes
the same path.
"""

import difflib
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field

import llm
from schemas import ClinicMatch, Extraction, RawReview, Record, Translation

# Canonical clinic registry: in production this is the platform's own
# verified-clinic table. Alias hits skip the LLM entirely.
CLINICS = {
    "ID Hospital": ["아이디병원", "id병원", "아이디성형외과", "id hospital"],
    "Wonjin Plastic Surgery Clinic": ["원진성형외과", "wonjin", "원진", "wonjin plastic surgery clinic"],
    "Kim Dermatology": ["김피부과", "kim dermatology"],
}
ALIAS_TO_CANONICAL = {a: c for c, aliases in CLINICS.items() for a in aliases}
MATCH_THRESHOLD = 0.80


@dataclass
class Item:
    raw: RawReview
    translation: Translation | None = None
    extraction: Extraction | None = None
    clinic: ClinicMatch | None = None
    needs_human_review: bool = False
    duplicate_of: str | None = None
    dropped: str = ""
    trust: int = 0
    trust_reasons: list[str] = field(default_factory=list)


def step_ingest(rows: list[dict]) -> list[Item]:
    """Validate scraped rows against the RawReview contract; drop empties."""
    items = []
    for row in rows:
        review = RawReview.model_validate(row)
        if review.text_ko.strip():
            items.append(Item(raw=review))
    return items


def step_translate(items: list[Item]) -> list[Item]:
    """LLM step. The Korean original is kept forever; dedup runs on it."""
    for it in items:
        if it.dropped:
            continue
        out = llm.call_json(
            "You translate Korean cosmetic-clinic reviews to English. "
            "Keep clinic names as written. Reply as {translated_en, source_lang}.",
            it.raw.text_ko,
        )
        it.translation = Translation.model_validate(out)
    return items


def step_extract(items: list[Item]) -> list[Item]:
    """LLM step with a validated schema. A review that fails validation
    goes to human review, never silently downstream."""
    for it in items:
        if it.dropped:
            continue
        out = llm.call_json(
            "You extract structured fields from a Korean clinic review. "
            "Reply as {procedure, clinic_raw, price_krw, sentiment, red_flags, confidence}. "
            "sentiment is positive|neutral|negative. Flag sponsored or "
            "incentivized language in red_flags.",
            it.raw.text_ko,
            model=llm.MODEL_BULK,
        )
        try:
            it.extraction = Extraction.model_validate(out)
        except Exception:
            it.needs_human_review = True
            it.extraction = Extraction(
                procedure="UNPARSED", clinic_raw="UNPARSED",
                sentiment="neutral", confidence=0.0,
            )
    return items


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


def step_normalize_clinic(items: list[Item]) -> list[Item]:
    """Alias table first (deterministic, free). Fuzzy candidates go to
    LLM adjudication; below-threshold confidence lands in the human
    review queue. Romanization variants and branch clinics make this the
    judgment-heavy step."""
    for it in items:
        if it.dropped or not it.extraction:
            continue
        raw_name = _norm(it.extraction.clinic_raw)
        if raw_name in {_norm(a) for a in ALIAS_TO_CANONICAL}:
            canonical = next(c for a, c in ALIAS_TO_CANONICAL.items() if _norm(a) == raw_name)
            it.clinic = ClinicMatch(canonical=canonical, confidence=1.0, via="alias_table")
            continue
        pool = list(ALIAS_TO_CANONICAL) + list(CLINICS)
        candidates = difflib.get_close_matches(it.extraction.clinic_raw, pool, n=3, cutoff=0.5)
        canonical_candidates = sorted({ALIAS_TO_CANONICAL.get(c, c) for c in candidates})
        if not canonical_candidates:
            it.clinic = ClinicMatch(canonical="UNMATCHED", confidence=0.0, via="unmatched")
            it.needs_human_review = True
            continue
        out = llm.call_json(
            "You adjudicate clinic entity matches for a Korean clinic registry. "
            "Given a raw clinic name and candidate canonical names, pick the "
            "match. Reply as {canonical, confidence}.",
            json.dumps({"clinic_raw": it.extraction.clinic_raw,
                        "candidates": canonical_candidates}, ensure_ascii=False),
            model=llm.MODEL_JUDGE,
        )
        it.clinic = ClinicMatch(canonical=out["canonical"],
                                confidence=float(out["confidence"]),
                                via="llm_adjudication")
        if it.clinic.confidence < MATCH_THRESHOLD:
            it.needs_human_review = True
    return items


def step_dedup(items: list[Item]) -> list[Item]:
    """Deterministic. The hash runs on the KOREAN source text:
    cross-posted reviews mutate less before translation than after."""
    seen: dict[str, str] = {}
    for it in items:
        if it.dropped:
            continue
        key = hashlib.md5(_norm(it.raw.text_ko).encode()).hexdigest()
        origin = f"{it.raw.source}/{it.raw.author}/{it.raw.posted}"
        if key in seen:
            it.duplicate_of = seen[key]
        else:
            seen[key] = origin
    return items


def step_trust_score(items: list[Item]) -> list[Item]:
    """Deterministic and explainable: every point has a named reason.
    An opaque score on medical content is not defensible."""
    for it in items:
        if it.dropped or not it.extraction:
            continue
        score, reasons = 50, []
        if it.extraction.confidence >= 0.8:
            score += 20; reasons.append("+20 high extraction confidence")
        if it.clinic and it.clinic.via == "alias_table":
            score += 15; reasons.append("+15 clinic matched in verified registry")
        if it.extraction.red_flags:
            score -= 25; reasons.append(f"-25 red flags: {','.join(it.extraction.red_flags)}")
        if it.duplicate_of:
            score -= 30; reasons.append("-30 duplicate of earlier post")
        it.trust = max(0, min(100, score))
        it.trust_reasons = reasons
    return items


def to_record(it: Item) -> Record:
    return Record(
        source=it.raw.source, author=it.raw.author, posted=it.raw.posted,
        text_ko=it.raw.text_ko,
        translated_en=it.translation.translated_en if it.translation else "",
        procedure=it.extraction.procedure, clinic=it.clinic.canonical,
        price_krw=it.extraction.price_krw, sentiment=it.extraction.sentiment,
        red_flags=it.extraction.red_flags, trust_score=it.trust,
        trust_reasons=it.trust_reasons,
        needs_human_review=it.needs_human_review,
        duplicate_of=it.duplicate_of,
    )


def run(rows: list[dict]) -> list[Record]:
    items = step_ingest(rows)
    for step in (step_translate, step_extract, step_normalize_clinic,
                 step_dedup, step_trust_score):
        items = step(items)
    return [to_record(it) for it in items if not it.dropped]


if __name__ == "__main__":
    rows = [json.loads(line) for line in open("fixtures.jsonl", encoding="utf-8")]
    records = run(rows)
    for r in records:
        print(r.model_dump_json())
    queued = sum(1 for r in records if r.needs_human_review)
    dupes = sum(1 for r in records if r.duplicate_of)
    print(f"# {len(records)} records, {queued} queued for human review, "
          f"{dupes} flagged duplicate", file=sys.stderr)
