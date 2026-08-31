"""Agent loop machinery test, offline: pytest test_agent.py -q"""

import agent


def test_scripted_agent_processes_full_batch():
    agent.STATE.update({"reviews": {}, "seen": {}, "emitted": [], "calls": 0})
    summary = agent.run_scripted()
    assert "4 reviews" in summary
    assert agent.STATE["calls"] == 22  # fetch + 4 reviews x 5 steps + report
    assert sum(1 for r in agent.STATE["emitted"] if r.duplicate_of) == 1
    assert sum(1 for r in agent.STATE["emitted"] if r.needs_human_review) == 1
