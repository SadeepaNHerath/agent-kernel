from __future__ import annotations

import json
from pathlib import Path

from tool import (
    approve_campaign_package,
    approve_caption_pack,
    approve_flyer,
    approve_flyer_content,
    create_event_context,
    draft_flyer_content,
    edit_flyer_content,
    generate_caption_pack,
    generate_flyer,
    ingest_direct_campaign_assets,
    publish_campaign,
    update_event_context,
)


def load_json(payload: str) -> dict:
    return json.loads(payload)


def test_full_campaign_workflow(monkeypatch, tmp_path):
    monkeypatch.setenv("CAMPAIGN_KERNEL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CAMPAIGN_KERNEL_MOCK_PUBLISH", "true")

    event = load_json(create_event_context("IDEALIZE AI Workshop", colors="blue, white"))
    event_id = event["event_id"]
    update = load_json(
        update_event_context(
            event_id,
            theme_notes="Modern student tech theme #StudentBuilders",
            sample_caption="Ready to build with AI? Register now. #IDEALIZE #AIWorkshop",
            caption_structure="Hook, details, CTA, hashtags",
        )
    )
    assert update["ok"] is True
    assert "#IDEALIZE" in update["style"]["default_hashtags"]
    assert "#StudentBuilders" in update["style"]["default_hashtags"]

    draft = load_json(
        draft_flyer_content(
            event_id,
            "Free AI workshop for university students on July 25 at University of Moratuwa. Register via link in bio.",
        )
    )
    campaign_id = draft["campaign"]["campaign_id"]
    assert draft["campaign"]["missing_fields"] == []

    approved_content = load_json(approve_flyer_content(event_id, campaign_id))
    assert approved_content["campaign"]["approvals"]["content"] is True

    flyer = load_json(generate_flyer(event_id, campaign_id, "premium but student-friendly"))
    flyer_path = Path(flyer["flyer"]["path"])
    assert flyer_path.exists()
    assert flyer_path.suffix == ".png"

    approved_flyer = load_json(approve_flyer(event_id, campaign_id))
    assert approved_flyer["campaign"]["approvals"]["flyer"] is True

    captions = load_json(generate_caption_pack(event_id, campaign_id))
    assert "instagram" in captions["caption_pack"]
    assert "linkedin" in captions["caption_pack"]
    assert captions["caption_pack"]["alt_text"]

    approved_caption = load_json(approve_caption_pack(event_id, campaign_id))
    assert approved_caption["campaign"]["approvals"]["caption"] is True

    approved_campaign = load_json(approve_campaign_package(event_id, campaign_id))
    assert approved_campaign["campaign"]["approvals"]["campaign"] is True

    published = load_json(publish_campaign(event_id, campaign_id, "instagram facebook linkedin whatsapp"))
    statuses = {result["target"]: result["status"] for result in published["results"]}
    assert statuses == {
        "instagram": "published",
        "facebook": "published",
        "linkedin": "published",
        "whatsapp": "ready",
    }


def test_missing_required_content_blocks_approval(monkeypatch, tmp_path):
    monkeypatch.setenv("CAMPAIGN_KERNEL_STATE_DIR", str(tmp_path / "state"))

    event = load_json(create_event_context("Sparse Event"))
    event_id = event["event_id"]
    draft = load_json(draft_flyer_content(event_id, "A community event for students"))
    campaign_id = draft["campaign"]["campaign_id"]
    assert set(draft["campaign"]["missing_fields"]) == {"date", "venue", "cta"}

    blocked = load_json(approve_flyer_content(event_id, campaign_id))
    assert blocked["ok"] is False
    assert blocked["blocked"] is True

    edited = load_json(
        edit_flyer_content(
            event_id,
            campaign_id,
            "date=July 25; venue=University of Moratuwa; cta=Register now",
        )
    )
    assert edited["campaign"]["missing_fields"] == []
    approved = load_json(approve_flyer_content(event_id, campaign_id))
    assert approved["ok"] is True


def test_direct_designer_editor_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CAMPAIGN_KERNEL_STATE_DIR", str(tmp_path / "state"))

    event = load_json(create_event_context("Design Ready Event"))
    event_id = event["event_id"]
    direct = load_json(
        ingest_direct_campaign_assets(
            event_id,
            campaign_brief="Leadership meetup on July 30 at Colombo. Register now.",
            flyer_path="output/final-flyer.png",
            caption_text="Join our leadership meetup. Register now. #Leadership",
        )
    )
    campaign = direct["campaign"]
    assert campaign["approvals"]["content"] is True
    assert campaign["approvals"]["flyer"] is True
    assert campaign["approvals"]["caption"] is True

    approved = load_json(approve_campaign_package(event_id, campaign["campaign_id"]))
    assert approved["campaign"]["approvals"]["campaign"] is True
