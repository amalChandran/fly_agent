"""Agent loop machinery tests, offline: pytest test_agent.py -q"""

import pytest

import agent


@pytest.fixture(autouse=True)
def fresh_state():
    agent.STATE.update({"reviews": {}, "seen": {}, "emitted": [], "calls": 0, "log": []})


def test_scripted_agent_processes_full_batch():
    summary = agent.run_scripted()
    assert "4 reviews" in summary
    assert agent.STATE["calls"] == 22  # fetch + 4 reviews x 5 steps + report
    assert sum(1 for r in agent.STATE["emitted"] if r.duplicate_of) == 1
    assert sum(1 for r in agent.STATE["emitted"] if r.needs_human_review) == 1


def test_planner_opens_with_fetch_closes_with_report():
    agent.run_scripted()
    names = [n for n, _ in agent.STATE["log"]]
    assert names[0] == "fetch_reviews"
    assert names[-1] == "report"
    assert names[1:6] == ["translate_review", "extract_fields", "match_clinic",
                          "check_duplicate", "finalize_review"]
