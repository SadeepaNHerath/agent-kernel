from __future__ import annotations

import importlib


def test_agents_use_openai_model_env(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.5")

    import agent

    importlib.reload(agent)

    assert agent.CAMPAIGN_MODEL == "gpt-5.5"
    assert all(campaign_agent.model == "gpt-5.5" for campaign_agent in agent.AGENTS)
