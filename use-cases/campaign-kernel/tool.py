from __future__ import annotations

import json
import os
import re
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentkernel.core import ToolContext
from PIL import Image, ImageDraw, ImageFont

SESSION_EVENT_KEY = "campaign_kernel.active_event"
SESSION_CAMPAIGN_KEY = "campaign_kernel.active_campaign"

STATE_DIR_ENV = "CAMPAIGN_KERNEL_STATE_DIR"
MOCK_PUBLISH_ENV = "CAMPAIGN_KERNEL_MOCK_PUBLISH"
LIVE_PUBLISH_ENV = "CAMPAIGN_KERNEL_LIVE_PUBLISH"

DEFAULT_STATE_DIR = ".campaign_kernel_state"
STATE_FILE = "state.json"
OUTPUT_DIR = "output"

REQUIRED_CONTENT_FIELDS = ["date", "venue", "cta"]
UNSAFE_TERMS = {"hate", "violence", "weapon", "illegal drugs", "scam", "fake certificate"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _state_dir() -> Path:
    return Path(os.environ.get(STATE_DIR_ENV, DEFAULT_STATE_DIR))


def _state_path() -> Path:
    return _state_dir() / STATE_FILE


def _output_dir() -> Path:
    return _state_dir() / OUTPUT_DIR


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {"events": {}, "version": 1}
    with path.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    state.setdefault("events", {})
    state.setdefault("version", 1)
    return state


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def _slugify(value: str, prefix: str = "ck") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return f"{prefix}-{slug or 'event'}"[:64].strip("-")


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,|]", value) if part.strip()]


def _extract_hashtags(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"#[A-Za-z0-9_]+", text)))


def _remember_session(key: str, value: str) -> None:
    try:
        cache = ToolContext.get().session.get_non_volatile_cache()
        cache.set(key, value)
    except RuntimeError:
        pass


def _get_event(state: dict[str, Any], event_id: str) -> dict[str, Any]:
    events = state.setdefault("events", {})
    if event_id not in events:
        raise ValueError(f"Unknown event_id: {event_id}")
    return events[event_id]


def _get_campaign(event: dict[str, Any], campaign_id: str) -> dict[str, Any]:
    campaigns = event.setdefault("campaigns", {})
    if campaign_id not in campaigns:
        raise ValueError(f"Unknown campaign_id: {campaign_id}")
    return campaigns[campaign_id]


def _next_campaign_id(event: dict[str, Any]) -> str:
    return f"CK-{len(event.setdefault('campaigns', {})) + 1:04d}"


def _validate_safe_text(*values: str) -> list[str]:
    combined = " ".join(value.lower() for value in values if value)
    return sorted(term for term in UNSAFE_TERMS if term in combined)


def _guess_date(text: str) -> str:
    month = (
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
        r"sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    )
    patterns = [
        rf"\b{month}\s+\d{{1,2}}(?:,\s*\d{{4}})?\b",
        rf"\b\d{{1,2}}\s+{month}(?:\s+\d{{4}})?\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return ""


def _guess_time(text: str) -> str:
    match = re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", text, flags=re.IGNORECASE)
    return match.group(0).strip() if match else ""


def _guess_venue(text: str) -> str:
    match = re.search(r"\bat\s+([^.;\n]+)", text, flags=re.IGNORECASE)
    if match:
        venue = match.group(1).strip()
        venue = re.split(r"\s+(?:on|from|for|with)\s+", venue, maxsplit=1, flags=re.IGNORECASE)[0]
        return venue.strip(" ,")
    return ""


def _guess_cta(text: str) -> str:
    lowered = text.lower()
    if "link in bio" in lowered:
        return "Register via link in bio"
    if "register" in lowered:
        return "Register now"
    if "join" in lowered:
        return "Join us"
    if "apply" in lowered:
        return "Apply now"
    return ""


def _guess_audience(text: str) -> str:
    lowered = text.lower()
    if "university student" in lowered or "students" in lowered:
        return "University students"
    if "school" in lowered:
        return "School students"
    if "volunteer" in lowered:
        return "Volunteers and community members"
    return "Community audience"


def _guess_title(text: str) -> str:
    cleaned = re.sub(r"\b(on|at)\b.+", "", text, flags=re.IGNORECASE).strip(" .")
    words = cleaned.split()
    if len(words) > 8:
        cleaned = " ".join(words[:8])
    return cleaned.title() if cleaned else "Upcoming Event"


def _missing_fields(content: dict[str, str]) -> list[str]:
    return [field for field in REQUIRED_CONTENT_FIELDS if not content.get(field, "").strip()]


def _normalize_targets(targets: str) -> list[str]:
    target_list = [target.lower().strip() for target in re.split(r"[\s,]+", targets) if target.strip()]
    allowed = {"instagram", "facebook", "linkedin", "whatsapp"}
    return [target for target in dict.fromkeys(target_list) if target in allowed]


def create_event_context(
    event_name: str,
    theme_notes: str = "",
    tone: str = "",
    colors: str = "",
    default_hashtags: str = "",
) -> str:
    """Create a reusable campaign event context."""
    state = _load_state()
    base_event_id = _slugify(event_name)
    event_id = base_event_id
    suffix = 2
    while event_id in state["events"]:
        event_id = f"{base_event_id}-{suffix}"
        suffix += 1

    event = {
        "event_id": event_id,
        "name": event_name.strip(),
        "created_at": _now(),
        "updated_at": _now(),
        "style": {
            "theme_notes": theme_notes.strip(),
            "tone": tone.strip() or "clear, energetic, professional student-event tone",
            "colors": _split_csv(colors),
            "logo_notes": [],
            "asset_refs": [],
            "sample_flyer_notes": [],
            "sample_captions": [],
            "caption_structure": "Hook, event details, CTA, hashtags",
            "default_hashtags": _extract_hashtags(default_hashtags),
        },
        "campaigns": {},
    }
    state["events"][event_id] = event
    _save_state(state)
    _remember_session(SESSION_EVENT_KEY, event_id)
    return _json({"ok": True, "event_id": event_id, "event": event})


def update_event_context(
    event_id: str,
    theme_notes: str = "",
    colors: str = "",
    logo_notes: str = "",
    asset_ref: str = "",
    sample_flyer_notes: str = "",
    sample_caption: str = "",
    caption_structure: str = "",
    default_hashtags: str = "",
) -> str:
    """Update event style, caption, logo, asset, and design context."""
    state = _load_state()
    event = _get_event(state, event_id)
    style = event.setdefault("style", {})

    if theme_notes:
        existing = style.get("theme_notes", "")
        style["theme_notes"] = f"{existing}\n{theme_notes.strip()}".strip()
        style["default_hashtags"] = list(
            dict.fromkeys(style.get("default_hashtags", []) + _extract_hashtags(theme_notes))
        )
    if colors:
        style["colors"] = list(dict.fromkeys(style.get("colors", []) + _split_csv(colors)))
    if logo_notes:
        style.setdefault("logo_notes", []).append(logo_notes.strip())
    if asset_ref:
        style.setdefault("asset_refs", []).append(asset_ref.strip())
    if sample_flyer_notes:
        style.setdefault("sample_flyer_notes", []).append(sample_flyer_notes.strip())
    if sample_caption:
        style.setdefault("sample_captions", []).append(sample_caption.strip())
        style["default_hashtags"] = list(
            dict.fromkeys(style.get("default_hashtags", []) + _extract_hashtags(sample_caption))
        )
    if caption_structure:
        style["caption_structure"] = caption_structure.strip()
    if default_hashtags:
        style["default_hashtags"] = list(
            dict.fromkeys(style.get("default_hashtags", []) + _extract_hashtags(default_hashtags))
        )

    event["updated_at"] = _now()
    _save_state(state)
    _remember_session(SESSION_EVENT_KEY, event_id)
    return _json({"ok": True, "event_id": event_id, "style": style})


def get_event_context(event_id: str) -> str:
    """Return stored context for an event."""
    state = _load_state()
    event = _get_event(state, event_id)
    _remember_session(SESSION_EVENT_KEY, event_id)
    return _json({"ok": True, "event": event})


def draft_flyer_content(
    event_id: str,
    campaign_brief: str,
    title: str = "",
    subtitle: str = "",
    date: str = "",
    time: str = "",
    venue: str = "",
    audience: str = "",
    cta: str = "",
    contact: str = "",
) -> str:
    """Create structured flyer content from a short event brief."""
    state = _load_state()
    event = _get_event(state, event_id)
    campaign_id = _next_campaign_id(event)

    content = {
        "title": title.strip() or _guess_title(campaign_brief),
        "subtitle": subtitle.strip() or "An event by " + event["name"],
        "date": date.strip() or _guess_date(campaign_brief),
        "time": time.strip() or _guess_time(campaign_brief),
        "venue": venue.strip() or _guess_venue(campaign_brief),
        "audience": audience.strip() or _guess_audience(campaign_brief),
        "cta": cta.strip() or _guess_cta(campaign_brief),
        "contact": contact.strip(),
        "key_message": campaign_brief.strip(),
        "editor_notes": "",
    }
    missing = _missing_fields(content)
    unsafe_terms = _validate_safe_text(campaign_brief, *content.values())

    campaign = {
        "campaign_id": campaign_id,
        "brief": campaign_brief.strip(),
        "created_at": _now(),
        "updated_at": _now(),
        "status": "needs_content_review" if missing or unsafe_terms else "content_draft",
        "content": content,
        "missing_fields": missing,
        "unsafe_terms": unsafe_terms,
        "approvals": {
            "content": False,
            "flyer": False,
            "caption": False,
            "campaign": False,
        },
        "flyer": {},
        "caption_pack": {},
        "publish_results": [],
        "revision_log": [{"stage": "content", "note": "Initial content draft created.", "at": _now()}],
    }
    event.setdefault("campaigns", {})[campaign_id] = campaign
    event["updated_at"] = _now()
    _save_state(state)
    _remember_session(SESSION_EVENT_KEY, event_id)
    _remember_session(SESSION_CAMPAIGN_KEY, campaign_id)
    return _json({"ok": True, "event_id": event_id, "campaign": campaign})


def edit_flyer_content(event_id: str, campaign_id: str, edit_instruction: str) -> str:
    """Apply user edits to the current flyer content draft."""
    state = _load_state()
    event = _get_event(state, event_id)
    campaign = _get_campaign(event, campaign_id)
    content = campaign.setdefault("content", {})

    editable_fields = {"title", "subtitle", "date", "time", "venue", "audience", "cta", "contact", "key_message"}
    updates: dict[str, str] = {}
    for raw_part in edit_instruction.split(";"):
        part = raw_part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            key = key.strip().lower().replace(" ", "_")
            if key in editable_fields:
                updates[key] = value.strip()
        else:
            match = re.search(
                r"change\s+(title|subtitle|date|time|venue|audience|cta|contact|key message)\s+to\s+(.+)",
                part,
                flags=re.IGNORECASE,
            )
            if match:
                key = match.group(1).lower().replace(" ", "_")
                updates[key] = match.group(2).strip()

    for key, value in updates.items():
        content[key] = value

    notes = content.get("editor_notes", "")
    content["editor_notes"] = f"{notes}\n{edit_instruction.strip()}".strip()
    campaign["missing_fields"] = _missing_fields(content)
    campaign["unsafe_terms"] = _validate_safe_text(campaign.get("brief", ""), *content.values())
    campaign["status"] = (
        "needs_content_review" if campaign["missing_fields"] or campaign["unsafe_terms"] else "content_draft"
    )
    campaign["updated_at"] = _now()
    campaign.setdefault("revision_log", []).append({"stage": "content", "note": edit_instruction.strip(), "at": _now()})
    event["updated_at"] = _now()
    _save_state(state)
    return _json({"ok": True, "campaign": campaign, "applied_updates": updates})


def approve_flyer_content(event_id: str, campaign_id: str) -> str:
    """Approve flyer content after required fields and safety checks pass."""
    state = _load_state()
    event = _get_event(state, event_id)
    campaign = _get_campaign(event, campaign_id)
    missing = campaign.get("missing_fields") or _missing_fields(campaign.get("content", {}))
    unsafe_terms = campaign.get("unsafe_terms") or []
    if missing or unsafe_terms:
        return _json({"ok": False, "blocked": True, "missing_fields": missing, "unsafe_terms": unsafe_terms})

    campaign["approvals"]["content"] = True
    campaign["status"] = "content_approved"
    campaign["updated_at"] = _now()
    campaign.setdefault("revision_log", []).append({"stage": "content", "note": "Content approved.", "at": _now()})
    event["updated_at"] = _now()
    _save_state(state)
    return _json({"ok": True, "campaign": campaign})


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _color_palette(colors: list[str]) -> tuple[str, str, str]:
    color_text = " ".join(colors).lower()
    if "#" in color_text:
        hex_colors = re.findall(r"#[0-9a-fA-F]{6}", color_text)
        if hex_colors:
            return hex_colors[0], hex_colors[1] if len(hex_colors) > 1 else "#0F172A", "#F8FAFC"
    if "purple" in color_text:
        return "#6D28D9", "#111827", "#F8FAFC"
    if "green" in color_text:
        return "#047857", "#0F172A", "#F7FEE7"
    if "red" in color_text:
        return "#B91C1C", "#111827", "#FEF2F2"
    return "#1D4ED8", "#111827", "#F8FAFC"


def _draw_wrapped(
    draw: ImageDraw.ImageDraw, text: str, xy: tuple[int, int], font: Any, fill: str, width: int, line_gap: int
) -> int:
    x, y = xy
    wrapped_lines: list[str] = []
    for paragraph in text.splitlines() or [text]:
        wrapped_lines.extend(textwrap.wrap(paragraph, width=width) or [""])
    for line in wrapped_lines:
        draw.text((x, y), line, fill=fill, font=font)
        bbox = draw.textbbox((x, y), line, font=font)
        y += bbox[3] - bbox[1] + line_gap
    return y


def generate_flyer(event_id: str, campaign_id: str, design_instruction: str = "") -> str:
    """Generate a PNG flyer from approved content and saved event style."""
    state = _load_state()
    event = _get_event(state, event_id)
    campaign = _get_campaign(event, campaign_id)
    if not campaign.get("approvals", {}).get("content"):
        return _json({"ok": False, "blocked": True, "reason": "Approve flyer content before generating the flyer."})

    style = event.get("style", {})
    content = campaign.get("content", {})
    primary, ink, background = _color_palette(style.get("colors", []))

    width, height = 1080, 1350
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 0, width, 160), fill=primary)
    draw.rectangle((0, 1180, width, height), fill=ink)
    draw.rectangle((72, 220, 130, 1080), fill=primary)

    title_font = _font(78, bold=True)
    subtitle_font = _font(38, bold=True)
    body_font = _font(34)
    small_font = _font(26)
    cta_font = _font(42, bold=True)

    logo_label = event["name"][:32]
    draw.rounded_rectangle((72, 48, 378, 118), radius=18, outline=background, width=3)
    draw.text((96, 68), logo_label, fill=background, font=small_font)

    y = 230
    y = _draw_wrapped(draw, content.get("title", "Upcoming Event"), (170, y), title_font, ink, 18, 8)
    y += 20
    y = _draw_wrapped(draw, content.get("subtitle", ""), (170, y), subtitle_font, primary, 28, 8)
    y += 54

    detail_lines = [
        f"Date: {content.get('date', 'TBA')}",
        f"Time: {content.get('time', 'TBA')}",
        f"Venue: {content.get('venue', 'TBA')}",
        f"For: {content.get('audience', 'Community audience')}",
    ]
    for line in detail_lines:
        draw.text((170, y), line, fill=ink, font=body_font)
        y += 56

    y += 18
    key_message = content.get("key_message", "")
    y = _draw_wrapped(draw, key_message, (170, y), body_font, ink, 34, 10)
    y += 44

    cta = content.get("cta", "Join us")
    draw.rounded_rectangle((170, y, 910, y + 104), radius=30, fill=primary)
    draw.text((210, y + 28), cta[:42], fill=background, font=cta_font)

    footer = "Generated by CampaignKernel"
    if design_instruction:
        footer = f"{footer} | {design_instruction[:72]}"
    draw.text((72, 1244), footer, fill=background, font=small_font)
    draw.text((72, 1286), "Review and approve before publishing.", fill=background, font=small_font)

    version = int(campaign.get("flyer", {}).get("version", 0)) + 1
    output_dir = _output_dir() / event_id / campaign_id
    output_dir.mkdir(parents=True, exist_ok=True)
    flyer_path = output_dir / f"flyer_v{version}.png"
    image.save(flyer_path)

    campaign["flyer"] = {
        "path": str(flyer_path),
        "version": version,
        "design_instruction": design_instruction.strip(),
        "generated_at": _now(),
    }
    campaign["approvals"]["flyer"] = False
    campaign["status"] = "flyer_draft"
    campaign["updated_at"] = _now()
    campaign.setdefault("revision_log", []).append(
        {"stage": "flyer", "note": design_instruction.strip() or "Generated flyer draft.", "at": _now()}
    )
    event["updated_at"] = _now()
    _save_state(state)
    return _json({"ok": True, "event_id": event_id, "campaign_id": campaign_id, "flyer": campaign["flyer"]})


def approve_flyer(event_id: str, campaign_id: str) -> str:
    """Approve the generated flyer."""
    state = _load_state()
    event = _get_event(state, event_id)
    campaign = _get_campaign(event, campaign_id)
    if not campaign.get("flyer", {}).get("path"):
        return _json({"ok": False, "blocked": True, "reason": "Generate a flyer before approving it."})
    campaign["approvals"]["flyer"] = True
    campaign["status"] = "flyer_approved"
    campaign["updated_at"] = _now()
    campaign.setdefault("revision_log", []).append({"stage": "flyer", "note": "Flyer approved.", "at": _now()})
    event["updated_at"] = _now()
    _save_state(state)
    return _json({"ok": True, "campaign": campaign})


def generate_caption_pack(event_id: str, campaign_id: str, user_direction: str = "") -> str:
    """Generate Instagram, Facebook, LinkedIn, and WhatsApp-ready captions."""
    state = _load_state()
    event = _get_event(state, event_id)
    campaign = _get_campaign(event, campaign_id)
    if not campaign.get("approvals", {}).get("flyer"):
        return _json({"ok": False, "blocked": True, "reason": "Approve the flyer before generating captions."})

    style = event.get("style", {})
    content = campaign.get("content", {})
    hashtags = style.get("default_hashtags", []) or ["#Event", "#Community", "#CampaignKernel"]
    hashtag_line = " ".join(hashtags[:12])
    title = content.get("title", "Upcoming Event")
    date = content.get("date", "TBA")
    venue = content.get("venue", "TBA")
    cta = content.get("cta", "Join us")
    tone = style.get("tone", "clear and energetic")
    caption_structure = style.get("caption_structure", "Hook, event details, CTA, hashtags")
    sample = (
        "\n\nStyle reference: " + style.get("sample_captions", [""])[-1][:180] if style.get("sample_captions") else ""
    )
    direction = f"\nDirection: {user_direction.strip()}" if user_direction.strip() else ""

    instagram = f"{title} is here.\n\n" f"Date: {date}\nVenue: {venue}\n\n" f"{cta}.\n\n{hashtag_line}"
    facebook = (
        f"We are excited to announce {title}.\n\n"
        f"{content.get('key_message', '').strip()}\n\n"
        f"Date: {date}\nVenue: {venue}\nAudience: {content.get('audience', 'Community audience')}\n\n"
        f"{cta}."
    )
    linkedin = (
        f"{event['name']} presents {title}.\n\n"
        f"This campaign is prepared in a {tone} style and follows the structure: {caption_structure}.\n\n"
        f"Date: {date}\nVenue: {venue}\n\n{cta}."
    )
    whatsapp = f"{title}\nDate: {date}\nVenue: {venue}\n{cta}"
    alt_text = (
        f"Event flyer for {title}. It announces the event date as {date}, venue as {venue}, "
        f"and call to action as {cta}."
    )

    campaign["caption_pack"] = {
        "instagram": instagram + direction,
        "facebook": facebook + sample + direction,
        "linkedin": linkedin + direction,
        "whatsapp_export": whatsapp,
        "hashtags": hashtags,
        "alt_text": alt_text,
        "style_used": {
            "tone": tone,
            "caption_structure": caption_structure,
        },
        "generated_at": _now(),
    }
    campaign["approvals"]["caption"] = False
    campaign["status"] = "caption_draft"
    campaign["updated_at"] = _now()
    campaign.setdefault("revision_log", []).append(
        {"stage": "caption", "note": user_direction.strip() or "Generated caption pack.", "at": _now()}
    )
    event["updated_at"] = _now()
    _save_state(state)
    return _json({"ok": True, "caption_pack": campaign["caption_pack"]})


def edit_caption_pack(event_id: str, campaign_id: str, edit_instruction: str) -> str:
    """Apply simple user edit notes to the caption pack."""
    state = _load_state()
    event = _get_event(state, event_id)
    campaign = _get_campaign(event, campaign_id)
    if not campaign.get("caption_pack"):
        return _json({"ok": False, "blocked": True, "reason": "Generate captions before editing them."})

    note = edit_instruction.strip()
    if "shorter" in note.lower():
        for platform in ["instagram", "facebook", "linkedin"]:
            text = campaign["caption_pack"].get(platform, "")
            campaign["caption_pack"][platform] = "\n".join(text.splitlines()[:6]).strip()
    else:
        campaign["caption_pack"]["editor_notes"] = (
            campaign["caption_pack"].get("editor_notes", "") + "\n" + note
        ).strip()

    campaign["approvals"]["caption"] = False
    campaign["status"] = "caption_draft"
    campaign["updated_at"] = _now()
    campaign.setdefault("revision_log", []).append({"stage": "caption", "note": note, "at": _now()})
    event["updated_at"] = _now()
    _save_state(state)
    return _json({"ok": True, "caption_pack": campaign["caption_pack"]})


def approve_caption_pack(event_id: str, campaign_id: str) -> str:
    """Approve the caption pack."""
    state = _load_state()
    event = _get_event(state, event_id)
    campaign = _get_campaign(event, campaign_id)
    if not campaign.get("caption_pack"):
        return _json({"ok": False, "blocked": True, "reason": "Generate captions before approving them."})
    campaign["approvals"]["caption"] = True
    campaign["status"] = "caption_approved"
    campaign["updated_at"] = _now()
    campaign.setdefault("revision_log", []).append({"stage": "caption", "note": "Caption pack approved.", "at": _now()})
    event["updated_at"] = _now()
    _save_state(state)
    return _json({"ok": True, "campaign": campaign})


def ingest_direct_campaign_assets(
    event_id: str,
    campaign_brief: str = "",
    flyer_path: str = "",
    caption_text: str = "",
) -> str:
    """Create a campaign from designer/editor-provided final flyer and caption inputs."""
    state = _load_state()
    event = _get_event(state, event_id)
    campaign_id = _next_campaign_id(event)
    unsafe_terms = _validate_safe_text(campaign_brief, caption_text)
    campaign = {
        "campaign_id": campaign_id,
        "brief": campaign_brief.strip() or "Designer/editor-provided campaign package.",
        "created_at": _now(),
        "updated_at": _now(),
        "status": "direct_package_review",
        "content": {
            "title": _guess_title(campaign_brief or caption_text),
            "subtitle": "Direct designer/editor input",
            "date": _guess_date(campaign_brief or caption_text),
            "time": _guess_time(campaign_brief or caption_text),
            "venue": _guess_venue(campaign_brief or caption_text),
            "audience": _guess_audience(campaign_brief or caption_text),
            "cta": _guess_cta(campaign_brief or caption_text),
            "contact": "",
            "key_message": campaign_brief.strip(),
            "editor_notes": "Direct package created from supplied flyer/caption.",
        },
        "missing_fields": [],
        "unsafe_terms": unsafe_terms,
        "approvals": {
            "content": True,
            "flyer": bool(flyer_path),
            "caption": bool(caption_text),
            "campaign": False,
        },
        "flyer": {"path": flyer_path.strip(), "version": 1, "source": "designer"} if flyer_path else {},
        "caption_pack": (
            {
                "instagram": caption_text.strip(),
                "facebook": caption_text.strip(),
                "linkedin": caption_text.strip(),
                "whatsapp_export": caption_text.strip(),
                "hashtags": _extract_hashtags(caption_text),
                "alt_text": f"Supplied event flyer for {event['name']}.",
                "source": "editor",
            }
            if caption_text
            else {}
        ),
        "publish_results": [],
        "revision_log": [{"stage": "direct", "note": "Direct campaign package ingested.", "at": _now()}],
    }
    event.setdefault("campaigns", {})[campaign_id] = campaign
    event["updated_at"] = _now()
    _save_state(state)
    _remember_session(SESSION_EVENT_KEY, event_id)
    _remember_session(SESSION_CAMPAIGN_KEY, campaign_id)
    return _json({"ok": True, "event_id": event_id, "campaign": campaign})


def approve_campaign_package(event_id: str, campaign_id: str) -> str:
    """Approve the final flyer and caption package before publishing/export."""
    state = _load_state()
    event = _get_event(state, event_id)
    campaign = _get_campaign(event, campaign_id)
    approvals = campaign.get("approvals", {})
    missing = [name for name in ["content", "flyer", "caption"] if not approvals.get(name)]
    if missing:
        return _json({"ok": False, "blocked": True, "missing_approvals": missing})
    if campaign.get("unsafe_terms"):
        return _json({"ok": False, "blocked": True, "unsafe_terms": campaign["unsafe_terms"]})
    approvals["campaign"] = True
    campaign["status"] = "campaign_approved"
    campaign["updated_at"] = _now()
    campaign.setdefault("revision_log", []).append(
        {"stage": "campaign", "note": "Final campaign approved.", "at": _now()}
    )
    event["updated_at"] = _now()
    _save_state(state)
    return _json({"ok": True, "campaign": campaign})


def _mock_publish(event_id: str, campaign_id: str, target: str) -> dict[str, Any]:
    return {
        "target": target,
        "mode": "mock",
        "status": "published",
        "url": f"mock://campaign-kernel/{target}/{event_id}/{campaign_id}",
        "published_at": _now(),
    }


def _post_form(url: str, data: dict[str, str], headers: dict[str, str] | None = None) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(url, data=encoded, headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return {"error": True, "status": error.code, "body": error.read().decode("utf-8", errors="replace")}
    except urllib.error.URLError as error:
        return {"error": True, "body": str(error)}


def _live_publish(target: str, campaign: dict[str, Any]) -> dict[str, Any]:
    caption_pack = campaign.get("caption_pack", {})
    public_media_url = os.environ.get("CAMPAIGN_KERNEL_PUBLIC_MEDIA_URL", "")
    graph_version = os.environ.get("META_GRAPH_VERSION", "v21.0")

    if target == "facebook":
        page_id = os.environ.get("FACEBOOK_PAGE_ID", "")
        token = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "")
        if not page_id or not token or not public_media_url:
            return {
                "target": target,
                "mode": "live",
                "status": "requires_credentials",
                "required": ["FACEBOOK_PAGE_ID", "FACEBOOK_PAGE_ACCESS_TOKEN", "CAMPAIGN_KERNEL_PUBLIC_MEDIA_URL"],
            }
        url = f"https://graph.facebook.com/{graph_version}/{page_id}/photos"
        response = _post_form(
            url, {"url": public_media_url, "caption": caption_pack.get("facebook", ""), "access_token": token}
        )
        return {"target": target, "mode": "live", "status": "sent", "response": response}

    if target == "instagram":
        user_id = os.environ.get("INSTAGRAM_USER_ID", "")
        token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
        if not user_id or not token or not public_media_url:
            return {
                "target": target,
                "mode": "live",
                "status": "requires_credentials",
                "required": ["INSTAGRAM_USER_ID", "INSTAGRAM_ACCESS_TOKEN", "CAMPAIGN_KERNEL_PUBLIC_MEDIA_URL"],
            }
        media_url = f"https://graph.facebook.com/{graph_version}/{user_id}/media"
        media = _post_form(
            media_url,
            {"image_url": public_media_url, "caption": caption_pack.get("instagram", ""), "access_token": token},
        )
        creation_id = media.get("id", "")
        if not creation_id:
            return {"target": target, "mode": "live", "status": "media_container_failed", "response": media}
        publish_url = f"https://graph.facebook.com/{graph_version}/{user_id}/media_publish"
        response = _post_form(publish_url, {"creation_id": creation_id, "access_token": token})
        return {"target": target, "mode": "live", "status": "sent", "response": response}

    if target == "linkedin":
        author_urn = os.environ.get("LINKEDIN_AUTHOR_URN", "")
        token = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
        if not author_urn or not token:
            return {
                "target": target,
                "mode": "live",
                "status": "requires_credentials",
                "required": ["LINKEDIN_AUTHOR_URN", "LINKEDIN_ACCESS_TOKEN"],
            }
        body = json.dumps(
            {
                "author": author_urn,
                "commentary": caption_pack.get("linkedin", ""),
                "visibility": "PUBLIC",
                "distribution": {
                    "feedDistribution": "MAIN_FEED",
                    "targetEntities": [],
                    "thirdPartyDistributionChannels": [],
                },
                "lifecycleState": "PUBLISHED",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://api.linkedin.com/rest/posts",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "LinkedIn-Version": os.environ.get("LINKEDIN_VERSION", "202606"),
                "X-Restli-Protocol-Version": "2.0.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return {"target": target, "mode": "live", "status": "sent", "response_status": response.status}
        except urllib.error.HTTPError as error:
            return {
                "target": target,
                "mode": "live",
                "status": "error",
                "body": error.read().decode("utf-8", errors="replace"),
            }
        except urllib.error.URLError as error:
            return {"target": target, "mode": "live", "status": "error", "body": str(error)}

    if target == "whatsapp":
        return {
            "target": target,
            "mode": "export",
            "status": "ready",
            "message": caption_pack.get("whatsapp_export", ""),
        }

    return {"target": target, "mode": "live", "status": "unsupported"}


def publish_campaign(event_id: str, campaign_id: str, targets: str, live_publish: str = "") -> str:
    """Publish or export an approved campaign package to selected targets."""
    state = _load_state()
    event = _get_event(state, event_id)
    campaign = _get_campaign(event, campaign_id)
    if not campaign.get("approvals", {}).get("campaign"):
        return _json({"ok": False, "blocked": True, "reason": "Approve the final campaign before publishing."})

    target_list = _normalize_targets(targets)
    if not target_list:
        return _json(
            {
                "ok": False,
                "blocked": True,
                "reason": "Choose one or more targets: instagram facebook linkedin whatsapp.",
            }
        )

    explicit_live = live_publish.strip().lower() in {"1", "true", "yes", "live"}
    live_enabled = explicit_live or os.environ.get(LIVE_PUBLISH_ENV, "").lower() in {"1", "true", "yes"}
    mock_enabled = os.environ.get(MOCK_PUBLISH_ENV, "true").lower() not in {"0", "false", "no"}
    use_mock = mock_enabled and not live_enabled

    results = []
    for target in target_list:
        if target == "whatsapp":
            result = _live_publish(target, campaign)
        elif use_mock:
            result = _mock_publish(event_id, campaign_id, target)
        else:
            result = _live_publish(target, campaign)
        results.append(result)

    campaign.setdefault("publish_results", []).extend(results)
    campaign["status"] = (
        "published"
        if all(result.get("status") in {"published", "ready", "sent"} for result in results)
        else "publish_attention"
    )
    campaign["updated_at"] = _now()
    event["updated_at"] = _now()
    _save_state(state)
    return _json({"ok": True, "event_id": event_id, "campaign_id": campaign_id, "results": results})


def get_campaign_status(event_id: str, campaign_id: str = "") -> str:
    """Return event or campaign status."""
    state = _load_state()
    event = _get_event(state, event_id)
    if campaign_id:
        return _json({"ok": True, "event_id": event_id, "campaign": _get_campaign(event, campaign_id)})
    summaries = {
        cid: {
            "status": campaign.get("status"),
            "approvals": campaign.get("approvals", {}),
            "brief": campaign.get("brief", ""),
        }
        for cid, campaign in event.get("campaigns", {}).items()
    }
    return _json({"ok": True, "event_id": event_id, "event_name": event.get("name"), "campaigns": summaries})
