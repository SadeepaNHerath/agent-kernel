from __future__ import annotations

import base64
import io
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
IMAGE_MODE_ENV = "CAMPAIGN_KERNEL_IMAGE_MODE"
IMAGE_MODEL_ENV = "CAMPAIGN_KERNEL_IMAGE_MODEL"

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


def _ensure_style_schema(style: dict[str, Any]) -> dict[str, Any]:
    style.setdefault("theme_notes", "")
    style.setdefault("tone", "clear, energetic, professional student-event tone")
    style.setdefault("colors", [])
    style.setdefault("logo_notes", [])
    style.setdefault("asset_refs", [])
    style.setdefault("sample_assets", [])
    style.setdefault("sample_flyer_notes", [])
    style.setdefault("sample_captions", [])
    style.setdefault("style_analysis", [])
    style.setdefault("caption_structure", "Hook, event details, CTA, hashtags")
    style.setdefault("default_hashtags", [])
    return style


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
            "sample_assets": [],
            "sample_flyer_notes": [],
            "sample_captions": [],
            "style_analysis": [],
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
    sample_asset_ref: str = "",
    style_analysis: str = "",
    caption_structure: str = "",
    default_hashtags: str = "",
) -> str:
    """Update event style, caption, logo, asset, and design context."""
    state = _load_state()
    event = _get_event(state, event_id)
    style = _ensure_style_schema(event.setdefault("style", {}))

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
    if sample_asset_ref:
        style.setdefault("sample_assets", []).append({"source": sample_asset_ref.strip(), "added_at": _now()})
    if sample_flyer_notes:
        style.setdefault("sample_flyer_notes", []).append(sample_flyer_notes.strip())
    if sample_caption:
        style.setdefault("sample_captions", []).append(sample_caption.strip())
        style["default_hashtags"] = list(
            dict.fromkeys(style.get("default_hashtags", []) + _extract_hashtags(sample_caption))
        )
    if style_analysis:
        style.setdefault("style_analysis", []).append({"summary": style_analysis.strip(), "analyzed_at": _now()})
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
    _ensure_style_schema(event.setdefault("style", {}))
    _save_state(state)
    _remember_session(SESSION_EVENT_KEY, event_id)
    return _json({"ok": True, "event": event})


def _dominant_hex_colors(file_path: str) -> list[str]:
    path = Path(file_path)
    if not file_path or not path.exists() or path.suffix.lower() == ".pdf":
        return []

    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((90, 90))
            buckets: dict[tuple[int, int, int], int] = {}
            pixels = image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata()
            for red, green, blue in pixels:
                key = (round(red / 32) * 32, round(green / 32) * 32, round(blue / 32) * 32)
                if max(key) > 240 or min(key) < 16:
                    continue
                buckets[key] = buckets.get(key, 0) + 1
    except OSError:
        return []

    colors = []
    for (red, green, blue), _count in sorted(buckets.items(), key=lambda item: item[1], reverse=True)[:5]:
        colors.append(f"#{max(0, min(red, 255)):02X}{max(0, min(green, 255)):02X}{max(0, min(blue, 255)):02X}")
    return list(dict.fromkeys(colors))


def _sample_layout(file_path: str) -> str:
    path = Path(file_path)
    if not file_path or not path.exists() or path.suffix.lower() == ".pdf":
        return "Uploaded sample reference"
    try:
        with Image.open(path) as image:
            width, height = image.size
    except OSError:
        return "Uploaded sample reference"

    ratio = width / max(height, 1)
    if 0.9 <= ratio <= 1.1:
        return "Square or near-square social post layout"
    if ratio < 0.9:
        return "Tall portrait poster layout"
    return "Wide banner-style layout"


def analyze_sample_flyer_context(
    event_id: str,
    file_name: str = "",
    caption: str = "",
    file_path: str = "",
    analysis_notes: str = "",
) -> str:
    """Store a sample flyer and extract reusable style context for future campaigns."""
    state = _load_state()
    event = _get_event(state, event_id)
    style = _ensure_style_schema(event.setdefault("style", {}))

    palette = _dominant_hex_colors(file_path)
    caption_hashtags = _extract_hashtags(caption)
    sample_asset = {
        "file_name": file_name.strip() or Path(file_path).name or "sample-flyer",
        "file_path": file_path.strip(),
        "caption": caption.strip(),
        "ingested_at": _now(),
    }

    existing_colors = style.get("colors", [])
    if palette:
        style["colors"] = list(dict.fromkeys(existing_colors + palette[:3]))
    if caption_hashtags:
        style["default_hashtags"] = list(dict.fromkeys(style.get("default_hashtags", []) + caption_hashtags))

    analysis = {
        "source_file": sample_asset["file_name"],
        "layout": _sample_layout(file_path),
        "color_palette": palette,
        "typography_feel": "bold title hierarchy with short supporting details",
        "hierarchy": "event name first, key detail block second, CTA last",
        "logo_placement": "top or footer brand area; keep sponsor marks separated from the main title",
        "cta_style": "short action line with high contrast",
        "caption_tone": style.get("tone", "clear, energetic, professional student-event tone"),
        "recurring_hashtags": caption_hashtags,
        "notes": analysis_notes.strip(),
    }
    if "minimal" in caption.lower():
        analysis["typography_feel"] = "clean minimal typography with generous spacing"
    if "premium" in caption.lower():
        analysis["typography_feel"] = "premium, confident typography with strong contrast"
    if "workshop" in caption.lower():
        analysis["hierarchy"] = "workshop title first, learning value second, registration CTA last"

    style.setdefault("sample_assets", []).append(sample_asset)
    style.setdefault("style_analysis", []).append(analysis)
    style.setdefault("sample_flyer_notes", []).append(
        f"{analysis['layout']}; {analysis['typography_feel']}; CTA: {analysis['cta_style']}"
    )

    event["updated_at"] = _now()
    _save_state(state)
    _remember_session(SESSION_EVENT_KEY, event_id)
    return _json(
        {
            "ok": True,
            "event_id": event_id,
            "sample_asset": sample_asset,
            "style_analysis": analysis,
            "style": style,
        }
    )


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
        "flyer_mode": "template",
        "flyer_preview_path": "",
        "latest_user_action": "content drafted",
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
    campaign["latest_user_action"] = "content edited"
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
    campaign["latest_user_action"] = "content approved"
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


def _centered_text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, font: Any, fill: str) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    x = box[0] + ((box[2] - box[0]) - (bbox[2] - bbox[0])) / 2
    y = box[1] + ((box[3] - box[1]) - (bbox[3] - bbox[1])) / 2 - 2
    draw.text((x, y), text, fill=fill, font=font)


def _latest_style_analysis(style: dict[str, Any]) -> dict[str, Any]:
    analyses = style.get("style_analysis") or []
    latest = analyses[-1] if analyses else {}
    return latest if isinstance(latest, dict) else {"summary": str(latest)}


def _event_palette(style: dict[str, Any]) -> tuple[str, str, str]:
    latest = _latest_style_analysis(style)
    colors = list(style.get("colors", []))
    colors.extend(latest.get("color_palette", []) if isinstance(latest.get("color_palette"), list) else [])
    return _color_palette(colors)


def _render_template_flyer(
    event: dict[str, Any],
    campaign: dict[str, Any],
    flyer_path: Path,
    design_instruction: str,
) -> None:
    style = _ensure_style_schema(event.setdefault("style", {}))
    content = campaign.get("content", {})
    latest_analysis = _latest_style_analysis(style)
    primary, ink, background = _event_palette(style)
    soft_panel = "#FFFFFF"
    muted = "#64748B"
    accent_fill = "#DBEAFE"
    if primary.lower().startswith("#0"):
        accent_fill = "#D1FAE5"
    if "premium" in design_instruction.lower():
        background = "#F8FAFC"
        soft_panel = "#FFFFFF"

    width, height = 1080, 1350
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 0, width, 156), fill=ink)
    draw.rectangle((0, 156, width, 176), fill=primary)
    draw.ellipse((735, -230, 1245, 280), fill=primary)
    draw.ellipse((795, -170, 1185, 220), outline=accent_fill, width=18)
    draw.rounded_rectangle((70, 215, 1010, 1118), radius=38, fill=soft_panel, outline="#E2E8F0", width=3)
    draw.rectangle((70, 215, 116, 1118), fill=primary)
    draw.rounded_rectangle((116, 215, 1010, 1118), radius=38, fill=soft_panel)

    label_font = _font(26, bold=True)
    meta_font = _font(30)
    detail_font = _font(34, bold=True)
    title_size = 74 if len(content.get("title", "")) < 48 else 62
    title_font = _font(title_size, bold=True)
    subtitle_font = _font(36, bold=True)
    body_font = _font(32)
    cta_font = _font(42, bold=True)
    footer_font = _font(25)

    logo_box = (78, 44, 455, 112)
    draw.rounded_rectangle(logo_box, radius=16, outline=background, width=3)
    _centered_text(draw, logo_box, event.get("name", "CampaignKernel")[:28], label_font, background)
    draw.text((780, 60), "READY TO PUBLISH", fill=background, font=label_font)

    y = 255
    draw.rounded_rectangle((155, y, 400, y + 48), radius=24, fill=accent_fill)
    _centered_text(draw, (155, y, 400, y + 48), "EVENT CAMPAIGN", label_font, ink)
    y += 78

    y = _draw_wrapped(draw, content.get("title", "Upcoming Event"), (155, y), title_font, ink, 17, 7)
    y += 18
    subtitle = content.get("subtitle", "")
    y = _draw_wrapped(draw, subtitle, (155, y), subtitle_font, primary, 28, 8)
    y += 42

    detail_boxes = [
        ("DATE", content.get("date", "TBA")),
        ("TIME", content.get("time", "TBA")),
        ("VENUE", content.get("venue", "TBA")),
        ("FOR", content.get("audience", "Community audience")),
    ]
    box_x = 155
    for index, (label, value) in enumerate(detail_boxes):
        row = index // 2
        col = index % 2
        x = box_x + col * 392
        detail_y = y + row * 124
        draw.rounded_rectangle((x, detail_y, x + 348, detail_y + 92), radius=22, fill="#F8FAFC", outline="#E2E8F0")
        draw.text((x + 24, detail_y + 18), label, fill=muted, font=label_font)
        draw.text((x + 24, detail_y + 48), value[:23], fill=ink, font=detail_font)
    y += 278

    key_message = content.get("key_message", "")
    if key_message:
        y = _draw_wrapped(draw, key_message, (155, y), body_font, ink, 36, 10)
        y += 28

    cta = content.get("cta", "Join us")
    draw.rounded_rectangle((155, 958, 925, 1065), radius=34, fill=primary)
    _centered_text(draw, (155, 958, 925, 1065), cta[:44], cta_font, background)

    analysis_line = ""
    if latest_analysis:
        analysis_line = latest_analysis.get("layout") or latest_analysis.get("summary", "")
    if design_instruction:
        analysis_line = design_instruction.strip()
    if analysis_line:
        draw.text((155, 1082), analysis_line[:78], fill=muted, font=footer_font)

    draw.rectangle((0, 1196, width, height), fill=ink)
    footer_lines = [
        content.get("contact", "") or "Generated by CampaignKernel",
        "Review the flyer and caption before publishing.",
    ]
    for index, line in enumerate(footer_lines):
        draw.text((74, 1244 + index * 42), line[:96], fill=background, font=footer_font)

    image.save(flyer_path)


def _fit_image_to_canvas(source_bytes: bytes, flyer_path: Path) -> None:
    with Image.open(io.BytesIO(source_bytes)) as source:
        source = source.convert("RGB")
        canvas_ratio = 1080 / 1350
        source_ratio = source.width / max(source.height, 1)
        if source_ratio > canvas_ratio:
            new_height = 1350
            new_width = int(new_height * source_ratio)
        else:
            new_width = 1080
            new_height = int(new_width / source_ratio)
        resized = source.resize((new_width, new_height), Image.Resampling.LANCZOS)
        left = max((new_width - 1080) // 2, 0)
        top = max((new_height - 1350) // 2, 0)
        cropped = resized.crop((left, top, left + 1080, top + 1350))
        cropped.save(flyer_path)


def _image_prompt(event: dict[str, Any], campaign: dict[str, Any], design_instruction: str) -> str:
    style = _ensure_style_schema(event.setdefault("style", {}))
    content = campaign.get("content", {})
    latest = _latest_style_analysis(style)
    return "\n".join(
        [
            "Create a polished vertical event flyer for social media.",
            f"Event brand: {event.get('name', '')}",
            f"Title: {content.get('title', '')}",
            f"Date: {content.get('date', '')}",
            f"Venue: {content.get('venue', '')}",
            f"CTA: {content.get('cta', '')}",
            f"Theme: {style.get('theme_notes', '')}",
            f"Sample style: {latest}",
            f"Design direction: {design_instruction}",
            "Use readable text hierarchy, strong contrast, clean spacing, and leave room for logos.",
        ]
    )


def _try_ai_flyer(event: dict[str, Any], campaign: dict[str, Any], flyer_path: Path, design_instruction: str) -> str:
    image_model = os.environ.get(IMAGE_MODEL_ENV) or os.environ.get("OPENAI_IMAGE_MODEL", "")
    if not image_model:
        return f"{IMAGE_MODEL_ENV} or OPENAI_IMAGE_MODEL is not configured."

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
        )
        response = client.images.generate(
            model=image_model,
            prompt=_image_prompt(event, campaign, design_instruction),
            size=os.environ.get("CAMPAIGN_KERNEL_IMAGE_SIZE", "1024x1536"),
        )
        image_data = response.data[0]
        if getattr(image_data, "b64_json", None):
            _fit_image_to_canvas(base64.b64decode(image_data.b64_json), flyer_path)
            return ""
        if getattr(image_data, "url", None):
            with urllib.request.urlopen(image_data.url, timeout=60) as remote_image:
                _fit_image_to_canvas(remote_image.read(), flyer_path)
            return ""
        return "Image API response did not include b64_json or url data."
    except Exception as error:
        return f"AI image generation failed, so template mode was used: {error}"


def generate_flyer(event_id: str, campaign_id: str, design_instruction: str = "") -> str:
    """Generate a PNG flyer from approved content and saved event style."""
    state = _load_state()
    event = _get_event(state, event_id)
    campaign = _get_campaign(event, campaign_id)
    if not campaign.get("approvals", {}).get("content"):
        return _json({"ok": False, "blocked": True, "reason": "Approve flyer content before generating the flyer."})

    version = int(campaign.get("flyer", {}).get("version", 0)) + 1
    output_dir = _output_dir() / event_id / campaign_id
    output_dir.mkdir(parents=True, exist_ok=True)
    flyer_path = output_dir / f"flyer_v{version}.png"

    requested_mode = os.environ.get(IMAGE_MODE_ENV, "template").strip().lower()
    mode = "template"
    fallback_reason = ""
    if requested_mode == "ai":
        fallback_reason = _try_ai_flyer(event, campaign, flyer_path, design_instruction)
        if fallback_reason:
            _render_template_flyer(event, campaign, flyer_path, design_instruction)
        else:
            mode = "ai"
    else:
        _render_template_flyer(event, campaign, flyer_path, design_instruction)

    campaign["flyer"] = {
        "path": str(flyer_path),
        "version": version,
        "design_instruction": design_instruction.strip(),
        "mode": mode,
        "requested_mode": requested_mode or "template",
        "fallback_reason": fallback_reason,
        "generated_at": _now(),
    }
    campaign["flyer_mode"] = mode
    campaign["flyer_preview_path"] = str(flyer_path)
    campaign["latest_user_action"] = "flyer generated"
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
    campaign["latest_user_action"] = "flyer approved"
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

    style = _ensure_style_schema(event.setdefault("style", {}))
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
    sample_analysis = _latest_style_analysis(style)
    if sample_analysis and not sample:
        sample = "\n\nStyle reference: " + (sample_analysis.get("hierarchy") or sample_analysis.get("layout", ""))[:180]
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
            "sample_style": sample_analysis,
        },
        "generated_at": _now(),
    }
    campaign["approvals"]["caption"] = False
    campaign["status"] = "caption_draft"
    campaign["latest_user_action"] = "captions generated"
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
    campaign["latest_user_action"] = "caption edited"
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
    campaign["latest_user_action"] = "caption approved"
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
        "flyer_mode": "direct",
        "flyer_preview_path": flyer_path.strip(),
        "latest_user_action": "direct package ingested",
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
    campaign["latest_user_action"] = "campaign approved"
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
    campaign["latest_user_action"] = "published or exported"
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
