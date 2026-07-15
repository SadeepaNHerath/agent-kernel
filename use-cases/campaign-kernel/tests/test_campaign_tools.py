from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from tool import (
    analyze_sample_flyer_context,
    approve_campaign_package,
    approve_caption_pack,
    approve_flyer,
    approve_flyer_content,
    create_event_context,
    draft_flyer_content,
    edit_caption_pack,
    edit_flyer_content,
    generate_caption_pack,
    generate_flyer,
    get_event_context,
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
    assert flyer["flyer"]["mode"] == "template"

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


def test_sample_flyer_context_is_stored(monkeypatch, tmp_path):
    monkeypatch.setenv("CAMPAIGN_KERNEL_STATE_DIR", str(tmp_path / "state"))

    event = load_json(create_event_context("Sample Aware Event"))
    event_id = event["event_id"]
    sample_path = tmp_path / "sample.png"
    Image.new("RGB", (1080, 1350), "#1D4ED8").save(sample_path)

    result = load_json(
        analyze_sample_flyer_context(
            event_id,
            file_name="sample.png",
            caption="sample for event premium workshop #IDEALIZE",
            file_path=str(sample_path),
        )
    )

    assert result["ok"] is True
    assert result["style"]["sample_assets"][0]["file_name"] == "sample.png"
    assert result["style"]["style_analysis"][0]["layout"] == "Tall portrait poster layout"
    assert "#IDEALIZE" in result["style"]["default_hashtags"]

    stored = load_json(get_event_context(event_id))
    assert stored["event"]["style"]["style_analysis"]
    assert stored["event"]["style"]["style_profile"]["sample_count"] == 1


def test_multiple_samples_build_event_style_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("CAMPAIGN_KERNEL_STATE_DIR", str(tmp_path / "state"))

    event = load_json(create_event_context("Multi Sample Event"))
    event_id = event["event_id"]

    first = tmp_path / "banded.png"
    image = Image.new("RGB", (1080, 1350), "#F8FAFC")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1080, 180), fill="#0F172A")
    draw.rectangle((0, 1170, 1080, 1350), fill="#0F172A")
    image.save(first)

    second = tmp_path / "side.png"
    image = Image.new("RGB", (1080, 1350), "#FFFFFF")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 150, 1350), fill="#1D4ED8")
    image.save(second)

    load_json(
        analyze_sample_flyer_context(
            event_id,
            file_name="banded.png",
            caption="Ready to build with AI? Register now.\n#IDEALIZE #AIWorkshop",
            file_path=str(first),
        )
    )
    result = load_json(
        analyze_sample_flyer_context(
            event_id,
            file_name="side.png",
            caption="Join the workshop. Link in bio.\n#IDEALIZE",
            file_path=str(second),
        )
    )

    profile = result["style"]["style_profile"]
    assert profile["sample_count"] == 2
    assert profile["accent_structure"] in {"header and footer bands", "side accent rail"}
    assert profile["caption_cta_phrases"]
    assert "#IDEALIZE" in result["style"]["default_hashtags"]


def test_ai_image_mode_falls_back_to_template(monkeypatch, tmp_path):
    monkeypatch.setenv("CAMPAIGN_KERNEL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CAMPAIGN_KERNEL_IMAGE_MODE", "ai")
    monkeypatch.delenv("CAMPAIGN_KERNEL_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_IMAGE_MODEL", raising=False)

    event = load_json(create_event_context("AI Fallback Event", colors="blue, white"))
    event_id = event["event_id"]
    draft = load_json(draft_flyer_content(event_id, "AI meetup on July 25 at Main Hall. Register now."))
    campaign_id = draft["campaign"]["campaign_id"]
    load_json(approve_flyer_content(event_id, campaign_id))

    flyer = load_json(generate_flyer(event_id, campaign_id, "try AI image"))

    assert flyer["flyer"]["requested_mode"] == "ai"
    assert flyer["flyer"]["mode"] == "template"
    assert flyer["flyer"]["fallback_reason"]
    assert Path(flyer["flyer"]["path"]).exists()


def test_flyer_prompt_changes_layout_and_output(monkeypatch, tmp_path):
    monkeypatch.setenv("CAMPAIGN_KERNEL_STATE_DIR", str(tmp_path / "state"))

    event = load_json(create_event_context("Prompt Responsive Event", colors="blue, white"))
    event_id = event["event_id"]
    draft = load_json(
        draft_flyer_content(
            event_id,
            "Advanced AI session for university students on July 25 at Main Hall. Register now.",
        )
    )
    campaign_id = draft["campaign"]["campaign_id"]
    load_json(approve_flyer_content(event_id, campaign_id))

    minimal = load_json(generate_flyer(event_id, campaign_id, "minimal clean layout with more whitespace"))
    bold = load_json(generate_flyer(event_id, campaign_id, "dark bold centered poster with large title and big CTA"))

    assert minimal["flyer"]["applied_direction"]["layout"] == "minimal"
    assert bold["flyer"]["applied_direction"]["layout"] == "bold_center"
    assert bold["flyer"]["applied_direction"]["mood"] == "dark"
    assert Path(minimal["flyer"]["path"]).read_bytes() != Path(bold["flyer"]["path"]).read_bytes()


def test_caption_edit_prompt_rewrites_caption(monkeypatch, tmp_path):
    monkeypatch.setenv("CAMPAIGN_KERNEL_STATE_DIR", str(tmp_path / "state"))

    event = load_json(create_event_context("Caption Event", colors="blue, white"))
    event_id = event["event_id"]
    load_json(
        update_event_context(
            event_id,
            sample_caption="Ready to build with AI? Register now.\n#IDEALIZE #AIWorkshop",
        )
    )
    draft = load_json(
        draft_flyer_content(
            event_id,
            "Free AI workshop for university students on July 25 at University of Moratuwa. Register via link in bio.",
        )
    )
    campaign_id = draft["campaign"]["campaign_id"]
    load_json(approve_flyer_content(event_id, campaign_id))
    load_json(generate_flyer(event_id, campaign_id, "premium student style"))
    load_json(approve_flyer(event_id, campaign_id))

    original = load_json(generate_caption_pack(event_id, campaign_id))
    edited = load_json(edit_caption_pack(event_id, campaign_id, "make it shorter and more professional, no hashtags"))

    assert edited["caption_pack"]["instagram"] != original["caption_pack"]["instagram"]
    assert len(edited["caption_pack"]["instagram"]) < len(original["caption_pack"]["instagram"])
    assert edited["caption_pack"]["hashtags"] == []
    assert edited["caption_pack"]["style_used"]["direction"]["tone"] == "professional"
