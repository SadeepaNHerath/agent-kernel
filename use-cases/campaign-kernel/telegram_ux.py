from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

ACTION_CODES = {
    "approve_content": "ac",
    "generate_flyer": "gf",
    "approve_flyer": "af",
    "generate_caption": "gc",
    "approve_caption": "apc",
    "approve_campaign": "ap",
    "publish": "pub",
    "export": "exp",
    "status": "st",
    "one_click_pack": "ocp",
    "impact": "imp",
    "report": "rep",
    "dashboard": "dash",
    "edit_content": "ec",
    "edit_flyer": "ef",
    "edit_caption": "eca",
    "regenerate_flyer": "rgf",
    "regenerate_caption": "rgc",
}
CODE_ACTIONS = {code: action for action, code in ACTION_CODES.items()}


@dataclass(frozen=True)
class CallbackAction:
    action: str
    event_id: str = ""
    campaign_id: str = ""
    value: str = ""


def load_payload(payload: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    try:
        data = json.loads(payload)
        return data if isinstance(data, dict) else {"ok": False, "error": str(payload)}
    except json.JSONDecodeError:
        return {"ok": False, "error": str(payload)}


def callback_data(action: str, event_id: str = "", campaign_id: str = "", value: str = "") -> str:
    code = ACTION_CODES.get(action, action)
    return "|".join(["ck", code, event_id, campaign_id, value]).rstrip("|")


def parse_callback_data(data: str) -> CallbackAction:
    parts = data.split("|")
    if len(parts) < 2 or parts[0] != "ck":
        raise ValueError("Unsupported callback data")
    action = CODE_ACTIONS.get(parts[1], parts[1])
    return CallbackAction(
        action=action,
        event_id=parts[2] if len(parts) > 2 else "",
        campaign_id=parts[3] if len(parts) > 3 else "",
        value=parts[4] if len(parts) > 4 else "",
    )


def button(text: str, action: str, event_id: str = "", campaign_id: str = "", value: str = "") -> dict[str, str]:
    return {"text": text, "callback_data": callback_data(action, event_id, campaign_id, value)}


def keyboard(rows: list[list[dict[str, str]]]) -> dict[str, list[list[dict[str, str]]]]:
    return {"inline_keyboard": rows}


def event_keyboard(event_id: str) -> dict[str, Any]:
    return keyboard([[button("Show Status", "status", event_id)]])


def content_keyboard(event_id: str, campaign_id: str) -> dict[str, Any]:
    return keyboard(
        [
            [button("Approve Content", "approve_content", event_id, campaign_id)],
            [button("One-Click Pack", "one_click_pack", event_id, campaign_id)],
            [
                button("Edit Content", "edit_content", event_id, campaign_id),
                button("Status", "status", event_id, campaign_id),
            ],
        ]
    )


def content_approved_keyboard(event_id: str, campaign_id: str) -> dict[str, Any]:
    return keyboard([[button("Generate Flyer", "generate_flyer", event_id, campaign_id)]])


def flyer_keyboard(event_id: str, campaign_id: str) -> dict[str, Any]:
    return keyboard(
        [
            [button("Approve Flyer", "approve_flyer", event_id, campaign_id)],
            [
                button("Edit Flyer", "edit_flyer", event_id, campaign_id),
                button("Regenerate", "regenerate_flyer", event_id, campaign_id),
            ],
        ]
    )


def flyer_approved_keyboard(event_id: str, campaign_id: str) -> dict[str, Any]:
    return keyboard([[button("Generate Captions", "generate_caption", event_id, campaign_id)]])


def caption_keyboard(event_id: str, campaign_id: str) -> dict[str, Any]:
    return keyboard(
        [
            [button("Approve Caption", "approve_caption", event_id, campaign_id)],
            [
                button("Edit Caption", "edit_caption", event_id, campaign_id),
                button("Regenerate", "regenerate_caption", event_id, campaign_id),
            ],
        ]
    )


def campaign_keyboard(event_id: str, campaign_id: str) -> dict[str, Any]:
    return keyboard(
        [
            [button("Approve Campaign", "approve_campaign", event_id, campaign_id)],
            [
                button("Impact", "impact", event_id, campaign_id),
                button("Report", "report", event_id, campaign_id),
            ],
        ]
    )


def publish_keyboard(event_id: str, campaign_id: str) -> dict[str, Any]:
    return keyboard(
        [
            [button("Publish IG/FB/LinkedIn", "publish", event_id, campaign_id, "social")],
            [button("WhatsApp Export", "export", event_id, campaign_id, "wa")],
            [button("Impact Dashboard", "dashboard", event_id, campaign_id)],
        ]
    )


def _campaign(data: dict[str, Any]) -> dict[str, Any]:
    campaign = data.get("campaign") or {}
    return campaign if isinstance(campaign, dict) else {}


def _content(campaign: dict[str, Any]) -> dict[str, str]:
    content = campaign.get("content") or {}
    return content if isinstance(content, dict) else {}


def _event_name(data: dict[str, Any]) -> str:
    event = data.get("event") or {}
    return event.get("name", "event") if isinstance(event, dict) else "event"


def _format_blocked(data: dict[str, Any]) -> str:
    if data.get("missing_fields"):
        return "I need these content details before approval: " + ", ".join(data["missing_fields"])
    if data.get("unsafe_terms"):
        return "This brief needs review because it contains blocked terms: " + ", ".join(data["unsafe_terms"])
    if data.get("missing_approvals"):
        return "Approve these first: " + ", ".join(data["missing_approvals"])
    return data.get("reason") or data.get("error") or "This step needs attention."


def start_message() -> str:
    return "\n".join(
        [
            "CampaignKernel is ready.",
            "",
            "Create an event with /new_event Event Name.",
            "Upload sample flyers with caption: sample for ck-event-id.",
            "Then send /brief ck-event-id what you need.",
            "",
            "I will guide approvals with buttons and send the flyer image here.",
        ]
    )


def help_message() -> str:
    return "\n".join(
        [
            "Main commands:",
            "/new_event Event Name",
            "/context theme=... colors=... sample_caption=...",
            "/brief Campaign brief",
            "/status",
            "",
            "Power commands still work with IDs, but the normal flow uses buttons.",
        ]
    )


def event_created_message(payload: str | dict[str, Any]) -> str:
    data = load_payload(payload)
    if not data.get("ok"):
        return "I could not create the event. " + _format_blocked(data)
    event_id = data.get("event_id", "")
    return "\n".join(
        [
            f"Event created: {_event_name(data)}",
            f"Event ID: {event_id}",
            "",
            f"Next, upload a sample flyer with caption: sample for {event_id}",
            f"Or send: /brief {event_id} your campaign brief",
        ]
    )


def context_updated_message(payload: str | dict[str, Any]) -> str:
    data = load_payload(payload)
    if not data.get("ok"):
        return "I could not update the event context. " + _format_blocked(data)
    style = data.get("style", {})
    colors = ", ".join(style.get("colors", [])[:5]) if isinstance(style, dict) else ""
    hashtags = " ".join(style.get("default_hashtags", [])[:8]) if isinstance(style, dict) else ""
    lines = ["Event style updated."]
    if colors:
        lines.append(f"Colors: {colors}")
    if hashtags:
        lines.append(f"Hashtags: {hashtags}")
    lines.append("Next: send a campaign brief or upload another sample flyer.")
    return "\n".join(lines)


def sample_saved_message(payload: str | dict[str, Any]) -> str:
    data = load_payload(payload)
    if not data.get("ok"):
        return "I could not read that sample flyer. " + _format_blocked(data)
    analysis = data.get("style_analysis", {})
    palette = ", ".join(analysis.get("color_palette", [])[:4]) if isinstance(analysis, dict) else ""
    style = data.get("style", {})
    profile = style.get("style_profile", {}) if isinstance(style, dict) else {}
    lines = [
        "Sample flyer saved.",
        f"Samples learned: {profile.get('sample_count', 1)}",
        f"Layout: {analysis.get('layout', 'sample reference')}",
        f"Pattern: {profile.get('accent_structure', analysis.get('accent_structure', 'event style'))}",
        f"Style: {analysis.get('typography_feel', 'event-style reference')}",
    ]
    if palette:
        lines.append(f"Palette: {palette}")
    lines.append("I will use this as context for the next flyer.")
    return "\n".join(lines)


def content_draft_message(payload: str | dict[str, Any]) -> str:
    data = load_payload(payload)
    if not data.get("ok"):
        return "Content draft is blocked. " + _format_blocked(data)
    campaign = _campaign(data)
    content = _content(campaign)
    lines = [
        "Content draft ready.",
        f"Campaign ID: {campaign.get('campaign_id', '')}",
        "",
        f"Title: {content.get('title', 'TBA')}",
        f"Date: {content.get('date', 'TBA') or 'TBA'}",
        f"Venue: {content.get('venue', 'TBA') or 'TBA'}",
        f"CTA: {content.get('cta', 'TBA') or 'TBA'}",
    ]
    missing = campaign.get("missing_fields") or []
    if missing:
        lines.extend(["", "Needs: " + ", ".join(missing)])
    lines.append("")
    lines.append("Approve the content or ask me to edit it.")
    return "\n".join(lines)


def content_approved_message(payload: str | dict[str, Any]) -> str:
    data = load_payload(payload)
    if not data.get("ok"):
        return "Content approval is blocked. " + _format_blocked(data)
    campaign = _campaign(data)
    return f"Content approved for {campaign.get('campaign_id', '')}.\nNext: generate the flyer."


def flyer_photo_caption(payload: str | dict[str, Any]) -> str:
    data = load_payload(payload)
    if not data.get("ok"):
        return "Flyer generation is blocked. " + _format_blocked(data)
    flyer = data.get("flyer", {})
    mode = flyer.get("mode", "template")
    caption = [
        "Flyer draft ready.",
        f"Campaign ID: {data.get('campaign_id', '')}",
        f"Version: {flyer.get('version', 1)}",
        f"Mode: {mode}",
    ]
    if flyer.get("fallback_reason"):
        caption.append("AI fallback: template renderer used.")
    caption.append("Review the image, then approve or regenerate.")
    return "\n".join(caption)


def campaign_pack_message(payload: str | dict[str, Any]) -> str:
    data = load_payload(payload)
    if not data.get("ok"):
        return "Campaign pack is blocked. " + _format_blocked(data)
    campaign_id = data.get("campaign_id", "")
    intelligence = data.get("intelligence", {})
    quality = intelligence.get("quality", {}) if isinstance(intelligence, dict) else {}
    sdgs = intelligence.get("sdg_badges", []) if isinstance(intelligence, dict) else []
    lines = [
        "Campaign pack ready.",
        f"Campaign ID: {campaign_id}",
    ]
    if sdgs:
        lines.append("SDGs: " + ", ".join(sdgs[:2]))
    if quality:
        lines.append(f"Quality score: {quality.get('score', 'N/A')} ({quality.get('grade', 'N/A')})")
    lines.append("Review the flyer and captions, then approve or edit.")
    return "\n".join(lines)


def impact_message(payload: str | dict[str, Any]) -> str:
    data = load_payload(payload)
    if not data.get("ok"):
        return "Impact analysis is blocked. " + _format_blocked(data)
    intelligence = data.get("intelligence", {})
    quality = intelligence.get("quality", {}) if isinstance(intelligence, dict) else {}
    sdgs = intelligence.get("sdg_badges", []) if isinstance(intelligence, dict) else []
    goals = intelligence.get("impact_goals", []) if isinstance(intelligence, dict) else []
    ctas = intelligence.get("optimized_ctas", []) if isinstance(intelligence, dict) else []
    lines = ["Campaign impact analysis."]
    if sdgs:
        lines.append("SDGs: " + ", ".join(sdgs[:3]))
    if quality:
        lines.append(f"Quality: {quality.get('score', 'N/A')} ({quality.get('grade', 'N/A')})")
    if goals:
        lines.append("Goals:")
        for goal in goals[:3]:
            lines.append(f"- {goal.get('metric')}: {goal.get('target')}")
    if ctas:
        lines.append("Best CTA: " + ctas[0])
    return "\n".join(lines)


def dashboard_message(payload: str | dict[str, Any]) -> str:
    data = load_payload(payload)
    if not data.get("ok"):
        return "Dashboard is unavailable. " + _format_blocked(data)
    dashboard = data.get("dashboard", {})
    sdgs = dashboard.get("sdgs_covered", {}) if isinstance(dashboard, dict) else {}
    lines = [
        "Impact dashboard.",
        f"Campaigns: {dashboard.get('campaigns', 0)}",
        f"Posts prepared: {dashboard.get('posts_prepared', 0)}",
        f"Estimated reach target: {dashboard.get('estimated_reach', 0)}",
        f"Average quality: {dashboard.get('average_quality_score', 0)}",
    ]
    if sdgs:
        lines.append("Top SDGs: " + ", ".join(list(sdgs.keys())[:3]))
    return "\n".join(lines)


def report_message(payload: str | dict[str, Any]) -> str:
    data = load_payload(payload)
    if not data.get("ok"):
        return "Report generation is blocked. " + _format_blocked(data)
    report = data.get("report", "")
    preview = report[:900].rstrip()
    if len(report) > len(preview):
        preview += "\n..."
    return "\n".join(["Impact report created.", f"Path: {data.get('path', '')}", "", preview])


def flyer_approved_message(payload: str | dict[str, Any]) -> str:
    data = load_payload(payload)
    if not data.get("ok"):
        return "Flyer approval is blocked. " + _format_blocked(data)
    campaign = _campaign(data)
    return f"Flyer approved for {campaign.get('campaign_id', '')}.\nNext: generate captions."


def caption_pack_message(payload: str | dict[str, Any]) -> str:
    data = load_payload(payload)
    if not data.get("ok"):
        return "Caption generation is blocked. " + _format_blocked(data)
    pack = data.get("caption_pack", {})
    preview = pack.get("instagram", "").strip()
    if len(preview) > 520:
        preview = preview[:500].rstrip() + "..."
    return "\n".join(["Caption draft ready.", "", "Instagram preview:", preview, "", "Approve or ask for edits."])


def caption_approved_message(payload: str | dict[str, Any]) -> str:
    data = load_payload(payload)
    if not data.get("ok"):
        return "Caption approval is blocked. " + _format_blocked(data)
    campaign = _campaign(data)
    return f"Caption approved for {campaign.get('campaign_id', '')}.\nNext: approve the full campaign package."


def campaign_approved_message(payload: str | dict[str, Any]) -> str:
    data = load_payload(payload)
    if not data.get("ok"):
        return "Final approval is blocked. " + _format_blocked(data)
    campaign = _campaign(data)
    return f"Campaign approved for {campaign.get('campaign_id', '')}.\nChoose where to publish or export."


def publish_results_message(payload: str | dict[str, Any]) -> str:
    data = load_payload(payload)
    if not data.get("ok"):
        return "Publish is blocked. " + _format_blocked(data)
    lines = ["Publish/export result:"]
    for result in data.get("results", []):
        target = result.get("target", "target")
        status = result.get("status", "unknown")
        mode = result.get("mode", "")
        lines.append(f"- {target}: {status} ({mode})")
        if result.get("required"):
            lines.append("  Needs: " + ", ".join(result["required"]))
    return "\n".join(lines)


def status_message(payload: str | dict[str, Any]) -> str:
    data = load_payload(payload)
    if not data.get("ok"):
        return "I could not load status. " + _format_blocked(data)
    if "campaign" not in data:
        campaigns = data.get("campaigns", {})
        if not campaigns:
            return f"{data.get('event_name', 'Event')} has no campaigns yet.\nNext: send /brief your campaign brief."
        lines = [f"Event: {data.get('event_name', '')}", f"Event ID: {data.get('event_id', '')}", "", "Campaigns:"]
        for campaign_id, campaign in list(campaigns.items())[:8]:
            lines.append(f"- {campaign_id}: {campaign.get('status', 'unknown')}")
        return "\n".join(lines)

    campaign = _campaign(data)
    approvals = campaign.get("approvals", {})
    lines = [
        f"Campaign {campaign.get('campaign_id', '')}",
        f"Status: {campaign.get('status', 'unknown')}",
        "Approvals: " + ", ".join(f"{name}={'yes' if approved else 'no'}" for name, approved in approvals.items()),
    ]
    if campaign.get("flyer", {}).get("path"):
        lines.append(f"Flyer: {campaign['flyer']['path']}")
    return "\n".join(lines)
