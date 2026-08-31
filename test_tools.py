"""Per-tool unit tests, offline, no API key.
Every test here failed against a broken version of the code before it
passed; a test that cannot fail is noise."""

import pytest

import agent
import llm
import pipeline
from schemas import Extraction, RawReview


@pytest.fixture(autouse=True)
def fresh_state():
    agent.STATE.update({"reviews": {}, "seen": {}, "emitted": [], "calls": 0, "log": []})


def row(text, source="s", author="a", posted="2026-01-01"):
    return {"source": source, "author": author, "posted": posted, "text_ko": text}


def test_fetch_drops_blank_rows():
    out = agent.execute("fetch_reviews", {})
    assert out["dropped_blank"] == 1
    assert len(out["review_ids"]) == 4


def test_unknown_tool_comes_back_as_error_not_crash():
    out = agent.execute("delete_database", {})
    assert "error" in out


def test_bad_args_surface_as_error_result():
    agent.execute("fetch_reviews", {})
    out = agent.execute("translate_review", {"review_id": 999})
    assert "error" in out


def test_exact_alias_is_free_and_certain():
    items = pipeline.step_ingest([row("아이디병원 코 수술 만족")])
    pipeline.step_extract(items)
    pipeline.step_normalize_clinic(items)
    assert items[0].clinic.via == "alias_table"
    assert items[0].clinic.confidence == 1.0


def test_unmatched_clinic_queues_never_slips_through():
    it = pipeline.Item(
        raw=RawReview(**row("x")),
        extraction=Extraction(procedure="p", clinic_raw="zzzz완전없는이름qqqq",
                              sentiment="neutral", confidence=0.9))
    pipeline.step_normalize_clinic([it])
    assert it.clinic.canonical == "UNMATCHED"
    assert it.needs_human_review is True


def test_dedup_ignores_whitespace_mutations():
    items = pipeline.step_ingest([
        row("아이디병원 코 수술 만족", source="s1"),
        row("아이디병원   코 수술   만족", source="s2"),  # respaced cross-post
    ])
    pipeline.step_dedup(items)
    assert items[0].duplicate_of is None
    assert items[1].duplicate_of == "s1/a/2026-01-01"


def test_trust_score_clamps_at_zero():
    it = pipeline.Item(
        raw=RawReview(**row("x")),
        extraction=Extraction(procedure="p", clinic_raw="c", sentiment="negative",
                              red_flags=["sponsored_language"], confidence=0.1),
        duplicate_of="s/a/2026-01-01")
    pipeline.step_trust_score([it])  # 50 - 25 - 30 would be -5
    assert it.trust == 0


def test_dedup_keys_on_korean_not_translation():
    # The offline translator truncates at 60 chars, so two different
    # reviews sharing a 60+ char prefix collide after translation but
    # not before it. Hashing the translation would wrongly merge them.
    prefix = ("강남에서 눈재수술 상담 받고 고민 많이 하다가 결국 여기로 "
              "결정했어요 원장님이 친절하시고 시설도 깨끗해서 좋았어요 정말 ")
    assert len(prefix) > 60  # must exceed the mock translator's cut
    items = pipeline.step_ingest([
        row(prefix + "만족합니다 흉터도 거의 없네요", source="s1"),
        row(prefix + "후회합니다 붓기가 너무 오래 가요", source="s2"),
    ])
    pipeline.step_translate(items)
    pipeline.step_dedup(items)
    assert items[0].duplicate_of is None
    assert items[1].duplicate_of is None


def test_schema_failure_routes_to_human_queue(monkeypatch):
    monkeypatch.setattr(llm, "call_json", lambda *a, **k: {"garbage": True})
    items = pipeline.step_ingest([row("아이디병원 후기")])
    pipeline.step_extract(items)
    assert items[0].needs_human_review is True
    assert items[0].extraction.procedure == "UNPARSED"
