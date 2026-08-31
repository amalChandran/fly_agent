"""Offline tests, no API key needed: pytest -q
Each test failed against a broken version of the code before it passed
here; a test that cannot fail is noise."""

import json

import llm
import pipeline


def rows():
    with open("fixtures.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_end_to_end_offline():
    records = pipeline.run(rows())
    assert len(records) == 4  # blank fixture dropped at ingest
    by_author = {r.author: r for r in records}
    assert by_author["user_b"].duplicate_of  # cross-post caught
    assert "sponsored_language" in by_author["user_d"].red_flags
    assert by_author["user_d"].trust_score < by_author["user_a"].trust_score


def test_dedup_hashes_korean_source():
    records = pipeline.run(rows())
    dup = next(r for r in records if r.duplicate_of)
    assert dup.duplicate_of == "naver_cafe/user_a/2026-07-02"
    assert dup.author == "user_b"


def test_fuzzy_clinic_goes_to_human_queue():
    records = pipeline.run(rows())
    wonjin = next(r for r in records if "Wonjin" in r.clinic)
    assert wonjin.needs_human_review is True  # 0.62 < 0.80 threshold


def test_alias_hit_never_calls_judge(monkeypatch):
    real = llm.call_json

    def guard(system, user, model=llm.MODEL_BULK):
        if "adjudicate" in system.lower():
            raise AssertionError("exact alias hit must not reach LLM adjudication")
        return real(system, user, model)

    monkeypatch.setattr(llm, "call_json", guard)
    row = {"source": "s", "author": "a", "posted": "2026-01-01",
           "text_ko": "아이디병원 코 수술 350만원 만족합니다"}
    records = pipeline.run([row])
    assert records[0].clinic == "ID Hospital"
    assert records[0].trust_score >= 80
