from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_web_workspace_and_docs_assets_exist():
    web_dir = PROJECT_ROOT / "web"

    index = (web_dir / "index.html").read_text(encoding="utf-8")
    docs = (web_dir / "docs.html").read_text(encoding="utf-8")
    app = (web_dir / "app.js").read_text(encoding="utf-8")

    assert "CampaignKernel Workspace" in index
    assert "CampaignKernel Documentation" in docs
    assert "/api/quick-pack" in app
    assert "/api/dashboard" in app
