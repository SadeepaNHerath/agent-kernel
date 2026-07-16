from __future__ import annotations

import base64
import colorsys
import io
import json
import os
import re
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
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

SDG_DEFINITIONS = [
    {
        "id": 1,
        "name": "No Poverty",
        "keywords": ["poverty", "donation", "relief", "low income", "fundraiser", "food drive", "livelihood"],
        "template": "community support",
    },
    {
        "id": 2,
        "name": "Zero Hunger",
        "keywords": ["hunger", "food", "nutrition", "meal", "agriculture", "food security"],
        "template": "relief and nutrition",
    },
    {
        "id": 3,
        "name": "Good Health and Well-being",
        "keywords": ["health", "medical", "wellbeing", "well-being", "mental health", "blood", "clinic", "fitness"],
        "template": "health awareness",
    },
    {
        "id": 4,
        "name": "Quality Education",
        "keywords": ["education", "workshop", "training", "students", "school", "university", "learning", "ai"],
        "template": "learning and upskilling",
    },
    {
        "id": 5,
        "name": "Gender Equality",
        "keywords": ["gender", "women", "girls", "equality", "inclusion", "female", "empowerment"],
        "template": "inclusive empowerment",
    },
    {
        "id": 6,
        "name": "Clean Water and Sanitation",
        "keywords": ["water", "sanitation", "hygiene", "wash", "clean water"],
        "template": "public health action",
    },
    {
        "id": 7,
        "name": "Affordable and Clean Energy",
        "keywords": ["energy", "solar", "renewable", "electricity", "clean energy"],
        "template": "innovation and sustainability",
    },
    {
        "id": 8,
        "name": "Decent Work and Economic Growth",
        "keywords": ["career", "jobs", "entrepreneur", "startup", "skills", "employment", "internship"],
        "template": "career growth",
    },
    {
        "id": 9,
        "name": "Industry, Innovation and Infrastructure",
        "keywords": ["innovation", "technology", "tech", "ai", "infrastructure", "engineering", "hackathon"],
        "template": "innovation showcase",
    },
    {
        "id": 10,
        "name": "Reduced Inequalities",
        "keywords": ["inequality", "access", "disabled", "disability", "inclusive", "rural", "marginalized"],
        "template": "access and inclusion",
    },
    {
        "id": 11,
        "name": "Sustainable Cities and Communities",
        "keywords": ["city", "community", "transport", "urban", "sustainable community"],
        "template": "community action",
    },
    {
        "id": 12,
        "name": "Responsible Consumption and Production",
        "keywords": ["recycling", "recycle", "waste", "plastic", "reuse", "sustainable consumption"],
        "template": "recycling and responsible action",
    },
    {
        "id": 13,
        "name": "Climate Action",
        "keywords": ["climate", "cleanup", "clean-up", "tree", "environment", "carbon", "green", "sustainability"],
        "template": "climate action",
    },
    {
        "id": 14,
        "name": "Life Below Water",
        "keywords": ["ocean", "marine", "beach", "river", "sea", "waterway"],
        "template": "marine protection",
    },
    {
        "id": 15,
        "name": "Life on Land",
        "keywords": ["forest", "wildlife", "biodiversity", "tree planting", "land", "nature"],
        "template": "nature protection",
    },
    {
        "id": 16,
        "name": "Peace, Justice and Strong Institutions",
        "keywords": ["peace", "justice", "rights", "governance", "legal", "civic"],
        "template": "civic awareness",
    },
    {
        "id": 17,
        "name": "Partnerships for the Goals",
        "keywords": ["partner", "sponsor", "collaboration", "coalition", "ngo", "aiesec"],
        "template": "partnership campaign",
    },
]

AUDIENCE_SEGMENTS = ["students", "parents", "donors", "volunteers", "companies", "community"]
PLATFORM_TARGETS = ["instagram", "facebook", "linkedin", "whatsapp", "email", "poster"]


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
    state.setdefault("calendar", [])
    state.setdefault("organization_profile", {})
    state.setdefault("partners", [])
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


def _content_text(event: dict[str, Any], campaign: dict[str, Any]) -> str:
    content = campaign.get("content", {})
    style = event.get("style", {})
    return " ".join(
        [
            event.get("name", ""),
            campaign.get("brief", ""),
            " ".join(str(value) for value in content.values()),
            style.get("theme_notes", ""),
            " ".join(style.get("sample_captions", [])),
        ]
    )


def _classify_sdgs(text: str, preferred_sdgs: list[int] | None = None) -> list[dict[str, Any]]:
    lowered = text.lower()
    preferred_sdgs = preferred_sdgs or []
    matches = []
    for definition in SDG_DEFINITIONS:
        matched_keywords = [keyword for keyword in definition["keywords"] if keyword in lowered]
        if definition["id"] in preferred_sdgs:
            matched_keywords.append("organization preference")
        if matched_keywords:
            score = min(100, 45 + len(set(matched_keywords)) * 15)
            matches.append(
                {
                    "id": definition["id"],
                    "name": definition["name"],
                    "score": score,
                    "template": definition["template"],
                    "matched_keywords": list(dict.fromkeys(matched_keywords))[:8],
                    "reason": f"Matches {definition['template']} signals: "
                    + ", ".join(list(dict.fromkeys(matched_keywords))[:5]),
                }
            )
    if not matches:
        matches.append(
            {
                "id": 17,
                "name": "Partnerships for the Goals",
                "score": 45,
                "template": "partnership campaign",
                "matched_keywords": ["general campaign"],
                "reason": "General community campaign with partnership potential.",
            }
        )
    return sorted(matches, key=lambda item: (-item["score"], item["id"]))[:3]


def _primary_sdg(sdgs: list[dict[str, Any]]) -> dict[str, Any]:
    return sdgs[0] if sdgs else {"id": 17, "name": "Partnerships for the Goals", "template": "partnership campaign"}


def _sdg_badges(sdgs: list[dict[str, Any]]) -> list[str]:
    return [f"SDG {sdg['id']}: {sdg['name']}" for sdg in sdgs[:3]]


def _impact_goals(content: dict[str, Any], sdgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    audience = content.get("audience", "Community audience")
    primary = _primary_sdg(sdgs)
    base_participants = 80 if "student" in audience.lower() else 50
    if primary["id"] in {3, 4, 8, 9}:
        base_participants += 40
    if primary["id"] in {12, 13, 14, 15}:
        return [
            {"metric": "volunteers", "target": 40, "why": "Enough people for visible community action."},
            {"metric": "waste_collected_or_actions", "target": 120, "why": "Track practical environmental impact."},
            {"metric": "social_reach", "target": 2500, "why": "Extend awareness beyond the event site."},
            {"metric": "partner_groups", "target": 3, "why": "Strengthen local SDG collaboration."},
        ]
    if primary["id"] in {1, 2}:
        return [
            {"metric": "donations_or_items", "target": 200, "why": "Make support measurable."},
            {"metric": "beneficiaries", "target": 75, "why": "Connect effort to people reached."},
            {"metric": "volunteers", "target": 25, "why": "Support collection and distribution."},
            {"metric": "social_reach", "target": 2000, "why": "Increase donor participation."},
        ]
    return [
        {"metric": "participants", "target": base_participants, "why": "Anchor the event around attendance."},
        {
            "metric": "signups",
            "target": max(40, base_participants // 2),
            "why": "Track conversion from campaign to action.",
        },
        {"metric": "volunteers", "target": 12, "why": "Ensure event operations are supported."},
        {"metric": "social_reach", "target": 3000, "why": "Measure awareness created by the campaign."},
    ]


def _optimized_ctas(content: dict[str, Any], sdgs: list[dict[str, Any]]) -> list[str]:
    primary = _primary_sdg(sdgs)
    title = content.get("title", "this campaign")
    if primary["id"] in {12, 13, 14, 15}:
        return ["Join the cleanup", "Volunteer for climate action", "Share this with your green team"]
    if primary["id"] in {1, 2}:
        return ["Donate today", "Contribute a care pack", "Volunteer for distribution"]
    if primary["id"] in {3}:
        return ["Register for the session", "Book your awareness slot", "Share with someone who needs this"]
    if primary["id"] in {4, 8, 9}:
        return [
            content.get("cta") or "Register now",
            f"Save your seat for {title}",
            "Share with a friend who wants to learn",
        ]
    return [content.get("cta") or "Join us", "Volunteer with the team", "Share the campaign"]


def _strategy(event: dict[str, Any], campaign: dict[str, Any], sdgs: list[dict[str, Any]]) -> dict[str, Any]:
    content = campaign.get("content", {})
    primary = _primary_sdg(sdgs)
    audience = content.get("audience", "community members")
    channels = ["Instagram", "WhatsApp"]
    if primary["id"] in {4, 8, 9, 17}:
        channels.append("LinkedIn")
    if primary["id"] in {1, 2, 3, 11, 13}:
        channels.append("Facebook")
    return {
        "angle": f"Position {content.get('title', 'the campaign')} as a {primary['template']} campaign tied to {primary['name']}.",
        "primary_audience": audience,
        "secondary_audiences": ["volunteers", "partners", "community members"],
        "recommended_channels": list(dict.fromkeys(channels)),
        "timeline": [
            "T-7 days: publish awareness post and WhatsApp teaser.",
            "T-3 days: share reminder with impact goal and CTA.",
            "T-1 day: publish final reminder and logistics.",
            "T+1 day: share impact report and thank partners.",
        ],
        "cta_focus": _optimized_ctas(content, sdgs)[0],
    }


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    def parse(hex_value: str) -> tuple[float, float, float]:
        match = re.search(r"#[0-9a-fA-F]{6}", hex_value or "")
        value = match.group(0).lstrip("#") if match else "1D4ED8"
        channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = []
        for channel in channels:
            linear.append(channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4)
        return linear[0], linear[1], linear[2]

    a = parse(hex_a)
    b = parse(hex_b)
    lum_a = 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2]
    lum_b = 0.2126 * b[0] + 0.7152 * b[1] + 0.0722 * b[2]
    light = max(lum_a, lum_b)
    dark = min(lum_a, lum_b)
    return round((light + 0.05) / (dark + 0.05), 2)


def _accessibility_check(
    event: dict[str, Any], campaign: dict[str, Any], caption_pack: dict[str, Any]
) -> dict[str, Any]:
    content = campaign.get("content", {})
    style = _ensure_style_schema(event.setdefault("style", {}))
    primary, _ink, background = _event_palette(style)
    contrast = _contrast_ratio(primary, background)
    title = content.get("title", "")
    alt_text = caption_pack.get("alt_text", "")
    issues = []
    if len(title) > 72:
        issues.append("Flyer title may be too long for mobile readability.")
    if not alt_text:
        issues.append("Alt text is missing.")
    if contrast < 3:
        issues.append("Primary color contrast may be weak.")
    combined_caption = " ".join(
        str(caption_pack.get(platform, "")) for platform in ["instagram", "facebook", "linkedin"]
    )
    if re.search(r"\b(everyone|all people|guaranteed|cure|instant)\b", combined_caption, flags=re.IGNORECASE):
        issues.append("Caption contains absolute wording that should be reviewed.")
    score = max(0, 100 - len(issues) * 18)
    return {
        "score": score,
        "contrast_ratio": contrast,
        "alt_text_present": bool(alt_text),
        "title_length": len(title),
        "inclusive_language": "review" if issues else "ok",
        "issues": issues,
        "checks": [
            "Readable title length",
            "Alt text",
            "Basic color contrast",
            "Inclusive/non-exaggerated wording",
        ],
    }


def _quality_score(
    content: dict[str, Any],
    sdgs: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    accessibility: dict[str, Any],
    caption_pack: dict[str, Any],
) -> dict[str, Any]:
    clarity = 100 - len(_missing_fields(content)) * 20
    sdg_alignment = min(100, _primary_sdg(sdgs).get("score", 50))
    cta_strength = 90 if content.get("cta") else 45
    accessibility_score = accessibility.get("score", 70)
    platform_readiness = 25 * sum(
        1 for key in ["instagram", "facebook", "linkedin", "whatsapp_export"] if caption_pack.get(key)
    )
    impact = 100 if goals else 40
    dimensions = {
        "clarity": clarity,
        "sdg_alignment": sdg_alignment,
        "cta_strength": cta_strength,
        "accessibility": accessibility_score,
        "platform_readiness": platform_readiness,
        "impact_goals": impact,
    }
    total = round(sum(dimensions.values()) / len(dimensions))
    return {
        "score": total,
        "grade": "A" if total >= 85 else "B" if total >= 70 else "C" if total >= 55 else "Needs work",
        "dimensions": dimensions,
        "recommendations": [
            "Add a measurable impact target." if impact < 100 else "Impact target is clear.",
            "Strengthen the CTA." if cta_strength < 80 else "CTA is action-oriented.",
            "Review accessibility issues." if accessibility_score < 85 else "Accessibility checks look healthy.",
        ],
    }


def _localized_captions(content: dict[str, Any], caption_pack: dict[str, Any]) -> dict[str, str]:
    title = content.get("title", "Upcoming Event")
    date = content.get("date", "TBA")
    venue = content.get("venue", "TBA")
    cta = content.get("cta", "Join us")
    english = caption_pack.get("instagram", f"{title}\nDate: {date}\nVenue: {venue}\n{cta}")
    sinhala = f"{title}\n\nදිනය: {date}\nස්ථානය: {venue}\n\n" f"{cta}.\nමෙම වැඩසටහනට එක්වී ඔබේ දායකත්වය ලබා දෙන්න."
    tamil = (
        f"{title}\n\nதேதி: {date}\nஇடம்: {venue}\n\n" f"{cta}.\nஇந்த முயற்சியில் இணைந்து உங்கள் பங்களிப்பை அளிக்கவும்."
    )
    return {"english": english, "sinhala": sinhala, "tamil": tamil}


def _audience_variants(content: dict[str, Any], sdgs: list[dict[str, Any]]) -> dict[str, str]:
    title = content.get("title", "this campaign")
    cta = content.get("cta", "Join us")
    primary = _primary_sdg(sdgs)
    return {
        "students": f"Build your skills and contribute to {primary['name']} through {title}. {cta}.",
        "parents": f"Support a meaningful opportunity for young people: {title}. {cta}.",
        "donors": f"Help scale measurable impact for {primary['name']} through {title}.",
        "volunteers": f"Your time can make this campaign stronger. Volunteer for {title}.",
        "companies": f"Partner with this campaign to support {primary['name']} and local community impact.",
        "community": f"Join the community around {title} and help create visible change.",
    }


def _platform_variants(content: dict[str, Any], caption_pack: dict[str, Any]) -> dict[str, str]:
    title = content.get("title", "Upcoming Event")
    date = content.get("date", "TBA")
    venue = content.get("venue", "TBA")
    cta = content.get("cta", "Join us")
    return {
        "instagram": caption_pack.get("instagram", ""),
        "facebook": caption_pack.get("facebook", ""),
        "linkedin": caption_pack.get("linkedin", ""),
        "whatsapp": caption_pack.get("whatsapp_export", ""),
        "email": f"Subject: {title}\n\nHi,\n\nWe are inviting you to {title} on {date} at {venue}.\n\n{cta}.",
        "poster": f"{title}\n{date} | {venue}\n{cta}",
    }


def _compliance_review(text: str, goals: list[dict[str, Any]]) -> dict[str, Any]:
    issues = []
    if _validate_safe_text(text):
        issues.append("Unsafe or prohibited wording detected.")
    if re.search(r"\bguarantee|guaranteed|cure|100%|instant result\b", text, flags=re.IGNORECASE):
        issues.append("Possible exaggerated or misleading claim.")
    for goal in goals:
        if goal.get("target", 0) > 100000:
            issues.append(f"Impact target for {goal.get('metric')} may be exaggerated.")
    return {"status": "attention" if issues else "ok", "issues": issues}


def _designer_feedback(style: dict[str, Any]) -> list[str]:
    profile = style.get("style_profile", {}) if isinstance(style.get("style_profile"), dict) else {}
    feedback = []
    if profile.get("sample_count", 0) < 2:
        feedback.append("Upload at least two sample flyers so the design pattern is more reliable.")
    if not profile.get("color_palette") and not style.get("colors"):
        feedback.append("Add brand colors or upload color-rich samples.")
    if "footer" not in profile.get("accent_structure", ""):
        feedback.append("Reserve a footer/logo area for partner marks and contact details.")
    if not feedback:
        feedback.append("Samples provide enough pattern context for a consistent campaign design.")
    return feedback


def _build_campaign_intelligence(
    event: dict[str, Any],
    campaign: dict[str, Any],
    organization_profile: dict[str, Any] | None = None,
    partners: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    style = _ensure_style_schema(event.setdefault("style", {}))
    org_profile = organization_profile if organization_profile is not None else event.get("organization_profile", {})
    partner_memory = partners if partners is not None else event.get("partners", [])
    preferred_sdgs = org_profile.get("preferred_sdgs", []) if isinstance(org_profile, dict) else []
    content = campaign.get("content", {})
    caption_pack = campaign.get("caption_pack", {})
    sdgs = _classify_sdgs(_content_text(event, campaign), preferred_sdgs)
    goals = _impact_goals(content, sdgs)
    accessibility = _accessibility_check(event, campaign, caption_pack)
    quality = _quality_score(content, sdgs, goals, accessibility, caption_pack)
    return {
        "sdgs": sdgs,
        "sdg_badges": _sdg_badges(sdgs),
        "sdg_template": _primary_sdg(sdgs).get("template", "campaign"),
        "impact_goals": goals,
        "strategy": _strategy(event, campaign, sdgs),
        "optimized_ctas": _optimized_ctas(content, sdgs),
        "multilingual_captions": _localized_captions(content, caption_pack),
        "audience_variants": _audience_variants(content, sdgs),
        "platform_variants": _platform_variants(content, caption_pack),
        "accessibility": accessibility,
        "quality": quality,
        "compliance": _compliance_review(_content_text(event, campaign), goals),
        "designer_feedback": _designer_feedback(style),
        "organization_memory": org_profile if isinstance(org_profile, dict) else {},
        "partner_memory": partner_memory[:8] if isinstance(partner_memory, list) else [],
        "generated_at": _now(),
    }


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


def _region_average(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[float, float, float]:
    region = image.crop(box)
    pixels = list(region.get_flattened_data() if hasattr(region, "get_flattened_data") else region.getdata())
    if not pixels:
        return 0, 0, 0
    red = sum(pixel[0] for pixel in pixels) / len(pixels)
    green = sum(pixel[1] for pixel in pixels) / len(pixels)
    blue = sum(pixel[2] for pixel in pixels) / len(pixels)
    return red, green, blue


def _brightness(rgb: tuple[float, float, float]) -> float:
    red, green, blue = rgb
    return (red * 0.299) + (green * 0.587) + (blue * 0.114)


def _image_visual_features(file_path: str) -> dict[str, Any]:
    path = Path(file_path)
    if not file_path or not path.exists() or path.suffix.lower() == ".pdf":
        return {
            "background_tone": "unknown",
            "color_energy": "unknown",
            "accent_structure": "uploaded reference",
            "logo_placement_hint": "top or footer brand area",
            "cta_style_hint": "high contrast CTA block",
        }

    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            original_size = image.size
            image.thumbnail((160, 200))
            width, height = image.size
            pixels = list(image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata())
    except OSError:
        return {
            "background_tone": "unknown",
            "color_energy": "unknown",
            "accent_structure": "uploaded reference",
            "logo_placement_hint": "top or footer brand area",
            "cta_style_hint": "high contrast CTA block",
        }

    if not pixels:
        return {}

    avg_rgb = (
        sum(pixel[0] for pixel in pixels) / len(pixels),
        sum(pixel[1] for pixel in pixels) / len(pixels),
        sum(pixel[2] for pixel in pixels) / len(pixels),
    )
    avg_brightness = _brightness(avg_rgb)
    avg_saturation = sum(
        colorsys.rgb_to_hsv(pixel[0] / 255, pixel[1] / 255, pixel[2] / 255)[1] for pixel in pixels
    ) / len(pixels)
    background_tone = "dark" if avg_brightness < 95 else "light" if avg_brightness > 185 else "balanced"
    color_energy = "vibrant" if avg_saturation > 0.42 else "muted" if avg_saturation < 0.18 else "balanced"

    top = _region_average(image, (0, 0, width, max(1, int(height * 0.16))))
    bottom = _region_average(image, (0, max(0, int(height * 0.84)), width, height))
    left = _region_average(image, (0, 0, max(1, int(width * 0.14)), height))
    right = _region_average(image, (max(0, int(width * 0.86)), 0, width, height))
    center = _region_average(
        image,
        (
            max(0, int(width * 0.28)),
            max(0, int(height * 0.28)),
            max(1, int(width * 0.72)),
            max(1, int(height * 0.72)),
        ),
    )
    center_brightness = _brightness(center)
    edge_bands = []
    for name, rgb in {"top": top, "bottom": bottom, "left": left, "right": right}.items():
        if abs(_brightness(rgb) - center_brightness) > 38:
            edge_bands.append(name)

    if {"top", "bottom"}.issubset(edge_bands):
        accent_structure = "header and footer bands"
        logo_hint = "top brand strip with footer partner area"
        cta_hint = "bottom CTA strip"
    elif "left" in edge_bands or "right" in edge_bands:
        accent_structure = "side accent rail"
        logo_hint = "top-left brand slot"
        cta_hint = "side-aligned CTA block"
    elif "top" in edge_bands:
        accent_structure = "strong header band"
        logo_hint = "top brand strip"
        cta_hint = "lower CTA button"
    elif "bottom" in edge_bands:
        accent_structure = "strong footer band"
        logo_hint = "footer brand or partner strip"
        cta_hint = "bottom CTA strip"
    elif background_tone == "dark":
        accent_structure = "full-bleed dark poster"
        logo_hint = "small high-contrast top logo"
        cta_hint = "bright CTA button"
    else:
        accent_structure = "clean central poster"
        logo_hint = "top or footer brand area"
        cta_hint = "high contrast CTA block"

    return {
        "original_size": original_size,
        "background_tone": background_tone,
        "color_energy": color_energy,
        "accent_structure": accent_structure,
        "edge_bands": edge_bands,
        "logo_placement_hint": logo_hint,
        "cta_style_hint": cta_hint,
    }


def _caption_pattern(caption: str) -> dict[str, Any]:
    lines = [line.strip() for line in caption.splitlines() if line.strip()]
    hashtags = _extract_hashtags(caption)
    lower = caption.lower()
    hook_style = "question hook" if "?" in caption[:120] else "direct announcement"
    if any(word in lower for word in ["ready", "join", "register", "apply", "build"]):
        hook_style = "action-led hook"
    if any(word in lower for word in ["we are excited", "proud to", "announce"]):
        hook_style = "formal announcement hook"
    cta_phrases = []
    for phrase in ["register now", "link in bio", "join us", "apply now", "save the date"]:
        if phrase in lower:
            cta_phrases.append(phrase)
    return {
        "line_count": len(lines),
        "hook_style": hook_style,
        "cta_phrases": cta_phrases,
        "hashtag_count": len(hashtags),
        "hashtags": hashtags,
        "has_hashtag_footer": bool(lines and all(part.startswith("#") for part in lines[-1].split())),
    }


def _analysis_items(style: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in style.get("style_analysis", []) if isinstance(item, dict)]


def _most_common(values: list[str], default: str) -> str:
    clean_values = [value for value in values if value]
    if not clean_values:
        return default
    return Counter(clean_values).most_common(1)[0][0]


def _style_profile(style: dict[str, Any]) -> dict[str, Any]:
    analyses = _analysis_items(style)
    palettes: list[str] = []
    for color in style.get("colors", []):
        palettes.append(str(color))
    for analysis in analyses:
        palettes.extend(analysis.get("color_palette", []) if isinstance(analysis.get("color_palette"), list) else [])

    caption_patterns = [
        _caption_pattern(caption)
        for caption in style.get("sample_captions", [])
        if caption.strip() and "sample for ck-" not in caption.lower()
    ]
    for asset in style.get("sample_assets", []):
        caption = asset.get("caption", "") if isinstance(asset, dict) else ""
        if caption.strip() and "sample for ck-" not in caption.lower():
            caption_patterns.append(_caption_pattern(caption))

    sample_count = max(len(style.get("sample_assets", [])), len(analyses))
    profile = {
        "sample_count": sample_count,
        "dominant_layout": _most_common(
            [analysis.get("layout", "") for analysis in analyses], "Tall portrait poster layout"
        ),
        "background_tone": _most_common([analysis.get("background_tone", "") for analysis in analyses], "balanced"),
        "accent_structure": _most_common(
            [analysis.get("accent_structure", "") for analysis in analyses], "clean central poster"
        ),
        "typography_feel": _most_common(
            [analysis.get("typography_feel", "") for analysis in analyses],
            "bold title hierarchy with short supporting details",
        ),
        "hierarchy": _most_common(
            [analysis.get("hierarchy", "") for analysis in analyses],
            "event title first, event value second, details third, CTA last",
        ),
        "logo_placement": _most_common(
            [analysis.get("logo_placement", "") for analysis in analyses], "top or footer brand area"
        ),
        "cta_style": _most_common([analysis.get("cta_style", "") for analysis in analyses], "high contrast CTA block"),
        "color_palette": list(dict.fromkeys(palettes))[:8],
        "caption_hook_style": _most_common(
            [pattern.get("hook_style", "") for pattern in caption_patterns], "action-led hook"
        ),
        "caption_hashtag_footer": (
            any(pattern.get("has_hashtag_footer") for pattern in caption_patterns) if caption_patterns else True
        ),
        "caption_cta_phrases": list(
            dict.fromkeys(phrase for pattern in caption_patterns for phrase in pattern.get("cta_phrases", []))
        )[:4],
    }
    return profile


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
    visual_features = _image_visual_features(file_path)
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
        "background_tone": visual_features.get("background_tone", "unknown"),
        "color_energy": visual_features.get("color_energy", "unknown"),
        "accent_structure": visual_features.get("accent_structure", "uploaded reference"),
        "edge_bands": visual_features.get("edge_bands", []),
        "typography_feel": "bold title hierarchy with short supporting details",
        "hierarchy": "event name first, key detail block second, CTA last",
        "logo_placement": visual_features.get("logo_placement_hint", "top or footer brand area"),
        "cta_style": visual_features.get("cta_style_hint", "short action line with high contrast"),
        "caption_pattern": _caption_pattern(caption) if caption.strip() else {},
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
    style["style_profile"] = _style_profile(style)

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


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: Any, max_width: int) -> str:
    clean = " ".join(str(text).split())
    if draw.textbbox((0, 0), clean, font=font)[2] <= max_width:
        return clean
    suffix = "..."
    while clean and draw.textbbox((0, 0), clean + suffix, font=font)[2] > max_width:
        clean = clean[:-1].rstrip()
    return (clean + suffix) if clean else suffix


def _latest_style_analysis(style: dict[str, Any]) -> dict[str, Any]:
    analyses = style.get("style_analysis") or []
    latest = analyses[-1] if analyses else {}
    return latest if isinstance(latest, dict) else {"summary": str(latest)}


def _event_palette(style: dict[str, Any]) -> tuple[str, str, str]:
    profile = style.get("style_profile") if isinstance(style.get("style_profile"), dict) else _style_profile(style)
    colors = list(profile.get("color_palette", [])) or list(style.get("colors", []))
    return _color_palette(colors)


def _flyer_direction(design_instruction: str, style: dict[str, Any]) -> dict[str, Any]:
    profile = style.get("style_profile") if isinstance(style.get("style_profile"), dict) else _style_profile(style)
    instruction = design_instruction.lower()
    layout = "structured"
    if "minimal" in instruction or "clean" in instruction or "simple" in instruction:
        layout = "minimal"
    elif "center" in instruction or "bold" in instruction or "big title" in instruction or "poster" in instruction:
        layout = "bold_center"
    elif "split" in instruction or "two column" in instruction or "2 column" in instruction:
        layout = "split"
    elif "side" in profile.get("accent_structure", ""):
        layout = "side_rail"
    elif "header" in profile.get("accent_structure", "") or "footer" in profile.get("accent_structure", ""):
        layout = "banded"

    if "dark" in instruction or profile.get("background_tone") == "dark":
        mood = "dark"
    elif "premium" in instruction or "professional" in instruction:
        mood = "premium"
    elif "fun" in instruction or "energetic" in instruction or "vibrant" in instruction:
        mood = "vibrant"
    else:
        mood = "light"

    cta_scale = (
        "large"
        if any(word in instruction for word in ["large cta", "bigger cta", "big cta", "highlight cta"])
        else "normal"
    )
    title_scale = (
        "large"
        if any(word in instruction for word in ["large title", "bigger title", "big title", "bold title"])
        else "normal"
    )
    density = "compact" if any(word in instruction for word in ["compact", "more details", "dense"]) else "airy"
    return {
        "layout": layout,
        "mood": mood,
        "cta_scale": cta_scale,
        "title_scale": title_scale,
        "density": density,
        "profile": profile,
    }


def _palette_for_direction(style: dict[str, Any], direction: dict[str, Any]) -> tuple[str, str, str, str, str]:
    primary, ink, background = _event_palette(style)
    accent = "#DBEAFE"
    surface = "#FFFFFF"
    if direction["mood"] == "dark":
        background = "#0B1220"
        surface = "#111827"
        ink = "#F8FAFC"
        accent = primary
    elif direction["mood"] == "premium":
        background = "#F8FAFC"
        surface = "#FFFFFF"
        ink = "#111827"
        accent = "#E0E7FF"
    elif direction["mood"] == "vibrant":
        background = "#F8FAFC"
        surface = "#FFFFFF"
        accent = "#FEF3C7"
    return primary, ink, background, surface, accent


def _text_lines(text: str, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [text]:
        lines.extend(textwrap.wrap(paragraph, width=width) or [""])
    return lines


def _draw_detail_chip(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    label: str,
    value: str,
    fonts: tuple[Any, Any],
    colors: tuple[str, str, str],
) -> None:
    label_font, value_font = fonts
    fill, outline, ink = colors
    value_ink = "#111827" if fill.upper() in {"#F8FAFC", "#FFFFFF"} else ink
    max_width = box[2] - box[0] - 44
    value_text = " ".join(str(value).split())
    fitted_font = value_font
    if draw.textbbox((0, 0), value_text, font=fitted_font)[2] > max_width:
        for size in range(int(getattr(value_font, "size", 30)) - 2, 21, -2):
            candidate_font = _font(size, bold=True)
            if draw.textbbox((0, 0), value_text, font=candidate_font)[2] <= max_width:
                fitted_font = candidate_font
                break
        else:
            value_text = _fit_text(draw, value, value_font, max_width)
    draw.rounded_rectangle(box, radius=22, fill=fill, outline=outline)
    draw.text((box[0] + 22, box[1] + 16), label, fill="#64748B", font=label_font)
    draw.text((box[0] + 22, box[1] + 48), value_text, fill=value_ink, font=fitted_font)


def _render_template_flyer(
    event: dict[str, Any],
    campaign: dict[str, Any],
    flyer_path: Path,
    design_instruction: str,
) -> dict[str, Any]:
    style = _ensure_style_schema(event.setdefault("style", {}))
    content = campaign.get("content", {})
    style["style_profile"] = _style_profile(style)
    direction = _flyer_direction(design_instruction, style)
    profile = direction["profile"]
    primary, ink, background, soft_panel, accent_fill = _palette_for_direction(style, direction)
    muted = "#64748B"
    outline = "#E2E8F0" if direction["mood"] != "dark" else "#334155"
    on_primary = "#FFFFFF"
    on_dark = "#F8FAFC"

    width, height = 1080, 1350
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)

    label_font = _font(26, bold=True)
    meta_font = _font(30)
    detail_font = _font(34, bold=True)
    title_size = 86 if direction["title_scale"] == "large" else 74
    if len(content.get("title", "")) > 48:
        title_size -= 12
    title_font = _font(title_size, bold=True)
    subtitle_font = _font(36, bold=True)
    body_font = _font(32)
    cta_font = _font(50 if direction["cta_scale"] == "large" else 42, bold=True)
    footer_font = _font(25)
    badge = "EVENT CAMPAIGN"
    if "workshop" in content.get("title", "").lower() or "workshop" in content.get("key_message", "").lower():
        badge = "WORKSHOP"
    elif "webinar" in content.get("key_message", "").lower():
        badge = "WEBINAR"
    sdg_badges = _sdg_badges(_classify_sdgs(_content_text(event, campaign)))
    sdg_badge = sdg_badges[0] if sdg_badges else "SDG-ALIGNED CAMPAIGN"

    detail_boxes = [
        ("DATE", content.get("date", "TBA")),
        ("TIME", content.get("time", "TBA")),
        ("VENUE", content.get("venue", "TBA")),
        ("FOR", content.get("audience", "Community audience")),
    ]

    logo_box = (78, 44, 455, 112)
    if direction["layout"] in {"banded", "side_rail", "structured"}:
        draw.rectangle((0, 0, width, 156), fill=ink if direction["mood"] != "dark" else "#020617")
        draw.rectangle((0, 156, width, 176), fill=primary)
        draw.rounded_rectangle(logo_box, radius=16, outline=background, width=3)
        _centered_text(draw, logo_box, event.get("name", "CampaignKernel")[:28], label_font, on_dark)
        draw.text((780, 60), "READY TO PUBLISH", fill=on_dark, font=label_font)

    if direction["layout"] == "minimal":
        draw.rectangle((0, 0, width, 18), fill=primary)
        draw.text((86, 80), event.get("name", "CampaignKernel")[:34], fill=muted, font=label_font)
        draw.text((86, 150), badge, fill=primary, font=label_font)
        y = 235
        y = _draw_wrapped(draw, content.get("title", "Upcoming Event"), (86, y), title_font, ink, 15, 10)
        y += 22
        y = _draw_wrapped(draw, content.get("subtitle", ""), (86, y), subtitle_font, primary, 29, 8)
        y += 54
        for label, value in detail_boxes:
            draw.text((86, y), f"{label}: {value}", fill=ink, font=meta_font)
            y += 48
        y += 32
        y = _draw_wrapped(draw, content.get("key_message", ""), (86, y), body_font, ink, 38, 11)
        cta_box = (86, 1050, 994, 1150) if direction["cta_scale"] == "large" else (86, 1068, 760, 1152)
        draw.rounded_rectangle(cta_box, radius=24, fill=primary)
        _centered_text(draw, cta_box, content.get("cta", "Join us")[:44], cta_font, on_primary)
    elif direction["layout"] == "bold_center":
        draw.ellipse((-250, -230, 520, 520), fill=primary)
        draw.ellipse((700, 920, 1270, 1490), fill=accent_fill)
        draw.rounded_rectangle((250, 96, 830, 156), radius=30, fill=soft_panel, outline=outline)
        _centered_text(draw, (250, 96, 830, 156), event.get("name", "CampaignKernel")[:36], label_font, ink)
        draw.rounded_rectangle((372, 238, 708, 290), radius=26, fill=primary)
        _centered_text(draw, (372, 238, 708, 290), badge, label_font, on_primary)
        title_lines = _text_lines(content.get("title", "Upcoming Event"), 13)
        y = 350
        for line in title_lines[:4]:
            bbox = draw.textbbox((0, 0), line, font=title_font)
            draw.text(((width - (bbox[2] - bbox[0])) / 2, y), line, fill=ink, font=title_font)
            y += bbox[3] - bbox[1] + 12
        y += 22
        subtitle_lines = _text_lines(content.get("subtitle", ""), 26)
        for line in subtitle_lines[:2]:
            bbox = draw.textbbox((0, 0), line, font=subtitle_font)
            draw.text(((width - (bbox[2] - bbox[0])) / 2, y), line, fill=primary, font=subtitle_font)
            y += bbox[3] - bbox[1] + 8
        y = 760
        for index, (label, value) in enumerate(detail_boxes[:3]):
            x = 130 + index * 280
            _draw_detail_chip(
                draw, (x, y, x + 240, y + 98), label, value, (label_font, meta_font), ("#F8FAFC", outline, ink)
            )
        cta_box = (150, 1048, 930, 1164)
        draw.rounded_rectangle(cta_box, radius=34, fill=primary)
        _centered_text(draw, cta_box, content.get("cta", "Join us")[:44], cta_font, on_primary)
    elif direction["layout"] == "split":
        draw.rectangle((0, 0, 420, height), fill=ink if direction["mood"] != "dark" else "#020617")
        draw.rectangle((420, 0, 450, height), fill=primary)
        draw.text((70, 70), event.get("name", "CampaignKernel")[:24], fill=on_dark, font=label_font)
        draw.text((70, 180), badge, fill=primary, font=label_font)
        y = 285
        y = _draw_wrapped(
            draw, content.get("title", "Upcoming Event"), (70, y), _font(60, bold=True), background, 10, 8
        )
        draw.rounded_rectangle((500, 145, 980, 780), radius=34, fill=soft_panel, outline=outline, width=3)
        y = 205
        for label, value in detail_boxes:
            _draw_detail_chip(
                draw, (540, y, 940, y + 94), label, value, (label_font, detail_font), ("#F8FAFC", outline, ink)
            )
            y += 124
        y = _draw_wrapped(draw, content.get("key_message", ""), (500, 845), body_font, ink, 28, 11)
        cta_box = (500, max(1025, y + 30), 980, max(1135, y + 140))
        draw.rounded_rectangle(cta_box, radius=30, fill=primary)
        _centered_text(draw, cta_box, content.get("cta", "Join us")[:34], cta_font, on_primary)
    else:
        if direction["layout"] == "side_rail":
            draw.rectangle((0, 176, 92, 1188), fill=primary)
            panel = (126, 230, 1010, 1118)
        else:
            draw.ellipse((735, -230, 1245, 280), fill=primary)
            draw.ellipse((795, -170, 1185, 220), outline=accent_fill, width=18)
            panel = (70, 215, 1010, 1118)
            if direction["layout"] == "banded":
                draw.rectangle((0, 1160, width, height), fill=ink if direction["mood"] != "dark" else "#020617")
        draw.rounded_rectangle(panel, radius=38, fill=soft_panel, outline=outline, width=3)
        if direction["layout"] != "side_rail":
            draw.rectangle((panel[0], panel[1], panel[0] + 46, panel[3]), fill=primary)
            draw.rounded_rectangle((panel[0] + 46, panel[1], panel[2], panel[3]), radius=38, fill=soft_panel)
        x0 = panel[0] + 85
        y = panel[1] + 40
        draw.rounded_rectangle((x0, y, x0 + 245, y + 48), radius=24, fill=accent_fill)
        _centered_text(draw, (x0, y, x0 + 245, y + 48), badge, label_font, ink)
        y += 78
        y = _draw_wrapped(draw, content.get("title", "Upcoming Event"), (x0, y), title_font, ink, 17, 7)
        y += 18
        y = _draw_wrapped(draw, content.get("subtitle", ""), (x0, y), subtitle_font, primary, 28, 8)
        y += 42
        for index, (label, value) in enumerate(detail_boxes):
            row = index // 2
            col = index % 2
            x = x0 + col * 392
            detail_y = y + row * 124
            _draw_detail_chip(
                draw,
                (x, detail_y, x + 348, detail_y + 92),
                label,
                value,
                (label_font, detail_font),
                ("#F8FAFC", outline, ink),
            )
        detail_bottom = y + 248
        y = detail_bottom + 34
        if content.get("key_message") and detail_bottom <= 860:
            message_lines = _text_lines(content.get("key_message", ""), 34)
            if len(message_lines) > 2:
                message_lines = message_lines[:2]
                message_lines[-1] = _fit_text(draw, message_lines[-1], body_font, panel[2] - x0 - 85)
            y = _draw_wrapped(draw, "\n".join(message_lines), (x0, y), body_font, ink, 34, 10)
        cta_height = 126 if direction["cta_scale"] == "large" else 104
        cta_y = max(990, min(1010, y + 38))
        cta_box = (x0, cta_y, panel[2] - 85, cta_y + cta_height)
        draw.rounded_rectangle(cta_box, radius=34, fill=primary)
        _centered_text(draw, cta_box, content.get("cta", "Join us")[:44], cta_font, on_primary)

    if direction["layout"] not in {"split"}:
        footer_fill = ink if direction["mood"] != "dark" else "#020617"
        draw.rectangle((0, 1196, width, height), fill=footer_fill)
    footer_lines = [
        content.get("contact", "") or event.get("name", "CampaignKernel"),
        f"Style learned from {profile.get('sample_count', 0)} sample(s): {profile.get('accent_structure', 'event style')}",
    ]
    for index, line in enumerate(footer_lines):
        draw.text((74, 1244 + index * 42), line[:96], fill=on_dark, font=footer_font)
    chip_text = sdg_badge[:34]
    chip_bbox = draw.textbbox((0, 0), chip_text, font=label_font)
    chip_width = min(420, chip_bbox[2] - chip_bbox[0] + 44)
    chip_x = width - chip_width - 72
    chip_y = 1240
    draw.rounded_rectangle((chip_x, chip_y, chip_x + chip_width, chip_y + 54), radius=27, fill=primary)
    draw.text((chip_x + 22, chip_y + 13), chip_text, fill=on_primary, font=label_font)

    image.save(flyer_path)
    return direction


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
    profile = style.get("style_profile") if isinstance(style.get("style_profile"), dict) else _style_profile(style)
    return "\n".join(
        [
            "Create a polished vertical event flyer for social media.",
            f"Event brand: {event.get('name', '')}",
            f"Title: {content.get('title', '')}",
            f"Date: {content.get('date', '')}",
            f"Venue: {content.get('venue', '')}",
            f"CTA: {content.get('cta', '')}",
            f"Theme: {style.get('theme_notes', '')}",
            f"Sample style profile: {profile}",
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
    applied_direction = _flyer_direction(design_instruction, _ensure_style_schema(event.setdefault("style", {})))
    if requested_mode == "ai":
        fallback_reason = _try_ai_flyer(event, campaign, flyer_path, design_instruction)
        if fallback_reason:
            applied_direction = _render_template_flyer(event, campaign, flyer_path, design_instruction)
        else:
            mode = "ai"
    else:
        applied_direction = _render_template_flyer(event, campaign, flyer_path, design_instruction)

    campaign["flyer"] = {
        "path": str(flyer_path),
        "version": version,
        "design_instruction": design_instruction.strip(),
        "mode": mode,
        "requested_mode": requested_mode or "template",
        "fallback_reason": fallback_reason,
        "applied_direction": {
            "layout": applied_direction.get("layout"),
            "mood": applied_direction.get("mood"),
            "cta_scale": applied_direction.get("cta_scale"),
            "title_scale": applied_direction.get("title_scale"),
            "density": applied_direction.get("density"),
        },
        "style_profile_used": applied_direction.get("profile", {}),
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


def _caption_direction(user_direction: str) -> dict[str, Any]:
    lower = user_direction.lower()
    return {
        "length": "short" if any(word in lower for word in ["short", "shorter", "concise", "brief"]) else "standard",
        "tone": (
            "professional"
            if any(word in lower for word in ["professional", "formal", "linkedin", "corporate"])
            else (
                "energetic"
                if any(word in lower for word in ["exciting", "energetic", "hype", "catchy", "bold"])
                else "friendly" if any(word in lower for word in ["friendly", "warm", "casual"]) else "event"
            )
        ),
        "urgency": any(word in lower for word in ["urgent", "limited", "last chance", "deadline", "today"]),
        "no_hashtags": any(word in lower for word in ["no hashtag", "without hashtag", "remove hashtag"]),
        "more_details": any(word in lower for word in ["more detail", "detailed", "include time", "include audience"]),
    }


def _caption_hook(title: str, event_name: str, cta: str, direction: dict[str, Any], profile: dict[str, Any]) -> str:
    if direction["tone"] == "professional":
        return f"{event_name} invites you to {title}."
    if direction["tone"] == "energetic":
        if profile.get("caption_hook_style") == "question hook":
            return f"Ready for {title}?"
        return f"{title} is here."
    if direction["tone"] == "friendly":
        return f"Come join us for {title}."
    if profile.get("caption_hook_style") == "formal announcement hook":
        return f"We are excited to announce {title}."
    if profile.get("caption_hook_style") == "question hook":
        return f"Ready for {title}?"
    if cta.lower().startswith(("register", "join", "apply")):
        return f"{cta}: {title}."
    return f"{title} is here."


def _caption_lines_for_details(content: dict[str, str], direction: dict[str, Any]) -> list[str]:
    lines = [f"Date: {content.get('date', 'TBA')}", f"Venue: {content.get('venue', 'TBA')}"]
    if direction["more_details"] or content.get("time"):
        lines.insert(1, f"Time: {content.get('time', 'TBA') or 'TBA'}")
    if direction["more_details"]:
        lines.append(f"For: {content.get('audience', 'Community audience')}")
    return lines


def _compose_caption_pack(event: dict[str, Any], campaign: dict[str, Any], user_direction: str = "") -> dict[str, Any]:
    style = _ensure_style_schema(event.setdefault("style", {}))
    style["style_profile"] = _style_profile(style)
    profile = style["style_profile"]
    content = campaign.get("content", {})
    hashtags = style.get("default_hashtags", []) or ["#Event", "#Community", "#CampaignKernel"]
    direction = _caption_direction(user_direction)
    if direction["no_hashtags"]:
        hashtags = []

    hashtag_line = " ".join(hashtags[:12])
    title = content.get("title", "Upcoming Event")
    date = content.get("date", "TBA")
    venue = content.get("venue", "TBA")
    cta = content.get("cta", "Join us")
    event_name = event.get("name", "CampaignKernel")
    tone = style.get("tone", "clear and energetic")
    caption_structure = style.get("caption_structure", "Hook, event details, CTA, hashtags")
    hook = _caption_hook(title, event_name, cta, direction, profile)
    detail_lines = _caption_lines_for_details(content, direction)
    urgency = "\nSeats are limited, so confirm your spot early." if direction["urgency"] else ""
    value_line = content.get("key_message", "").strip()

    if direction["length"] == "short":
        instagram_parts = [hook, *detail_lines[:2], cta]
        facebook_parts = [hook, f"{date} at {venue}.", cta]
        linkedin_parts = [f"{event_name}: {title}", f"{date} at {venue}.", cta]
    elif direction["tone"] == "professional":
        instagram_parts = [hook, value_line, *detail_lines, cta]
        facebook_parts = [
            hook,
            value_line,
            *detail_lines,
            f"Audience: {content.get('audience', 'Community audience')}",
            cta,
        ]
        linkedin_parts = [
            f"{event_name} presents {title}.",
            value_line,
            "This campaign follows the saved event communication style and keeps the approval-ready details clear.",
            *detail_lines,
            cta,
        ]
    elif direction["tone"] == "energetic":
        instagram_parts = [
            hook,
            "A focused session for students ready to build, learn, and move fast.",
            *detail_lines,
            cta,
        ]
        facebook_parts = [
            hook,
            value_line,
            "Bring your curiosity and get ready for a practical event experience.",
            *detail_lines,
            cta,
        ]
        linkedin_parts = [f"{event_name} presents {title}.", value_line, *detail_lines, cta]
    else:
        instagram_parts = [hook, *detail_lines, cta]
        facebook_parts = [
            hook,
            value_line,
            *detail_lines,
            f"Audience: {content.get('audience', 'Community audience')}",
            cta,
        ]
        linkedin_parts = [
            f"{event_name} presents {title}.",
            f"Prepared in a {tone} style using the event structure: {caption_structure}.",
            *detail_lines,
            cta,
        ]

    def join_parts(parts: list[str], include_hashtags: bool = True) -> str:
        clean_parts = [part for part in parts if part]
        text = "\n\n".join(clean_parts) + urgency
        if include_hashtags and hashtag_line:
            text = (
                f"{text}\n\n{hashtag_line}"
                if profile.get("caption_hashtag_footer", True)
                else f"{hashtag_line}\n\n{text}"
            )
        return text.strip()

    whatsapp = f"{title}\nDate: {date}\nVenue: {venue}\n{cta}"
    if direction["more_details"] and content.get("time"):
        whatsapp = f"{title}\nDate: {date}\nTime: {content.get('time')}\nVenue: {venue}\n{cta}"

    return {
        "instagram": join_parts(instagram_parts),
        "facebook": join_parts(facebook_parts),
        "linkedin": join_parts(linkedin_parts, include_hashtags=False),
        "whatsapp_export": whatsapp,
        "hashtags": hashtags,
        "alt_text": (
            f"Event flyer for {title}. It announces the event date as {date}, venue as {venue}, "
            f"and call to action as {cta}."
        ),
        "style_used": {
            "tone": tone,
            "caption_structure": caption_structure,
            "sample_style": profile,
            "direction": direction,
        },
        "generated_at": _now(),
    }


def generate_caption_pack(event_id: str, campaign_id: str, user_direction: str = "") -> str:
    """Generate Instagram, Facebook, LinkedIn, and WhatsApp-ready captions."""
    state = _load_state()
    event = _get_event(state, event_id)
    campaign = _get_campaign(event, campaign_id)
    if not campaign.get("approvals", {}).get("flyer"):
        return _json({"ok": False, "blocked": True, "reason": "Approve the flyer before generating captions."})

    campaign["caption_pack"] = _compose_caption_pack(event, campaign, user_direction)
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
    """Regenerate the caption pack according to user edit notes."""
    state = _load_state()
    event = _get_event(state, event_id)
    campaign = _get_campaign(event, campaign_id)
    if not campaign.get("caption_pack"):
        return _json({"ok": False, "blocked": True, "reason": "Generate captions before editing them."})

    note = edit_instruction.strip()
    campaign["caption_pack"] = _compose_caption_pack(event, campaign, note)
    campaign["caption_pack"]["editor_notes"] = note

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


def enrich_campaign_intelligence(event_id: str, campaign_id: str) -> str:
    """Analyze campaign SDGs, impact, strategy, accessibility, quality, and audience/platform variants."""
    state = _load_state()
    event = _get_event(state, event_id)
    campaign = _get_campaign(event, campaign_id)
    campaign["intelligence"] = _build_campaign_intelligence(
        event,
        campaign,
        state.get("organization_profile", {}),
        state.get("partners", []),
    )
    campaign["updated_at"] = _now()
    event["updated_at"] = _now()
    _save_state(state)
    return _json(
        {"ok": True, "event_id": event_id, "campaign_id": campaign_id, "intelligence": campaign["intelligence"]}
    )


def generate_one_click_campaign_pack(
    event_id: str,
    campaign_id: str,
    design_instruction: str = "",
    caption_direction: str = "",
) -> str:
    """Generate flyer, captions, SDG intelligence, impact goals, variants, and quality score in one action."""
    state = _load_state()
    event = _get_event(state, event_id)
    campaign = _get_campaign(event, campaign_id)
    missing = campaign.get("missing_fields") or _missing_fields(campaign.get("content", {}))
    unsafe_terms = campaign.get("unsafe_terms") or []
    if missing or unsafe_terms:
        return _json({"ok": False, "blocked": True, "missing_fields": missing, "unsafe_terms": unsafe_terms})
    _save_state(state)

    approve_flyer_content(event_id, campaign_id)
    flyer_result = json.loads(generate_flyer(event_id, campaign_id, design_instruction))
    approve_flyer(event_id, campaign_id)
    caption_result = json.loads(generate_caption_pack(event_id, campaign_id, caption_direction))
    intelligence_result = json.loads(enrich_campaign_intelligence(event_id, campaign_id))
    state = _load_state()
    event = _get_event(state, event_id)
    campaign = _get_campaign(event, campaign_id)
    campaign["status"] = "one_click_pack_ready"
    campaign["latest_user_action"] = "one-click campaign pack generated"
    campaign["updated_at"] = _now()
    event["updated_at"] = _now()
    _save_state(state)
    return _json(
        {
            "ok": True,
            "event_id": event_id,
            "campaign_id": campaign_id,
            "flyer": flyer_result.get("flyer", {}),
            "caption_pack": caption_result.get("caption_pack", {}),
            "intelligence": intelligence_result.get("intelligence", {}),
            "campaign": campaign,
        }
    )


def generate_campaign_impact_report(event_id: str, campaign_id: str, report_format: str = "markdown") -> str:
    """Create a post-campaign impact report for judges or team records."""
    state = _load_state()
    event = _get_event(state, event_id)
    campaign = _get_campaign(event, campaign_id)
    intelligence = campaign.get("intelligence") or _build_campaign_intelligence(
        event,
        campaign,
        state.get("organization_profile", {}),
        state.get("partners", []),
    )
    campaign["intelligence"] = intelligence
    content = campaign.get("content", {})
    flyer = campaign.get("flyer", {})
    publish_results = campaign.get("publish_results", [])
    sdg_lines = [f"- {badge}" for badge in intelligence.get("sdg_badges", [])] or ["- Not classified yet"]
    goal_lines = [
        f"- {goal.get('metric')}: {goal.get('target')} ({goal.get('why')})"
        for goal in intelligence.get("impact_goals", [])
    ] or ["- No measurable goals generated yet"]
    caption_keys = ", ".join(
        key
        for key in ["instagram", "facebook", "linkedin", "whatsapp_export"]
        if campaign.get("caption_pack", {}).get(key)
    )
    platform_lines = [
        f"- {result.get('target')}: {result.get('status')} ({result.get('mode')})" for result in publish_results
    ] or ["- Not published yet"]
    accessibility_issues = [
        f"- Issue: {issue}" for issue in intelligence.get("accessibility", {}).get("issues", [])
    ] or ["- No blocking accessibility issues found"]
    report = "\n".join(
        [
            f"# Campaign Impact Report: {content.get('title', campaign_id)}",
            "",
            f"Event: {event.get('name')}",
            f"Campaign ID: {campaign_id}",
            f"Status: {campaign.get('status')}",
            "",
            "## SDGs Addressed",
            *sdg_lines,
            "",
            "## Impact Goals",
            *goal_lines,
            "",
            "## Campaign Materials",
            f"- Flyer: {flyer.get('path', 'Not generated')}",
            f"- Captions: {caption_keys or 'Not generated'}",
            "",
            "## Platforms Used",
            *platform_lines,
            "",
            "## Quality Score",
            f"- {intelligence.get('quality', {}).get('score', 'N/A')} ({intelligence.get('quality', {}).get('grade', 'N/A')})",
            "",
            "## Accessibility",
            f"- Score: {intelligence.get('accessibility', {}).get('score', 'N/A')}",
            *accessibility_issues,
        ]
    )
    output_dir = _output_dir() / event_id / campaign_id
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / ("impact_report.md" if report_format.lower() != "json" else "impact_report.json")
    if report_format.lower() == "json":
        report_path.write_text(json.dumps({"report": report, "intelligence": intelligence}, indent=2), encoding="utf-8")
    else:
        report_path.write_text(report, encoding="utf-8")
    campaign["impact_report"] = {"path": str(report_path), "format": report_format, "generated_at": _now()}
    campaign["updated_at"] = _now()
    event["updated_at"] = _now()
    _save_state(state)
    return _json(
        {"ok": True, "event_id": event_id, "campaign_id": campaign_id, "report": report, "path": str(report_path)}
    )


def get_impact_dashboard(event_id: str = "") -> str:
    """Return aggregate campaign, SDG, platform, volunteer, and reach metrics."""
    state = _load_state()
    events = state.get("events", {})
    selected_events = {event_id: _get_event(state, event_id)} if event_id else events
    sdg_counter: Counter[str] = Counter()
    platform_counter: Counter[str] = Counter()
    campaign_count = 0
    posts_prepared = 0
    volunteer_target = 0
    reach_target = 0
    quality_scores = []
    for event in selected_events.values():
        for campaign in event.get("campaigns", {}).values():
            campaign_count += 1
            intelligence = campaign.get("intelligence") or _build_campaign_intelligence(
                event,
                campaign,
                state.get("organization_profile", {}),
                state.get("partners", []),
            )
            for sdg in intelligence.get("sdgs", []):
                sdg_counter[f"SDG {sdg['id']}: {sdg['name']}"] += 1
            caption_pack = campaign.get("caption_pack", {})
            posts_prepared += sum(
                1 for key in ["instagram", "facebook", "linkedin", "whatsapp_export"] if caption_pack.get(key)
            )
            for result in campaign.get("publish_results", []):
                platform_counter[result.get("target", "unknown")] += 1
            for goal in intelligence.get("impact_goals", []):
                if "volunteer" in goal.get("metric", ""):
                    volunteer_target += int(goal.get("target", 0))
                if "reach" in goal.get("metric", ""):
                    reach_target += int(goal.get("target", 0))
            if intelligence.get("quality", {}).get("score"):
                quality_scores.append(intelligence["quality"]["score"])
    dashboard = {
        "campaigns": campaign_count,
        "sdgs_covered": dict(sdg_counter),
        "posts_prepared": posts_prepared,
        "published_platforms": dict(platform_counter),
        "volunteers_targeted": volunteer_target,
        "estimated_reach": reach_target,
        "average_quality_score": round(sum(quality_scores) / len(quality_scores)) if quality_scores else 0,
        "calendar_items": len(state.get("calendar", [])),
    }
    return _json({"ok": True, "event_id": event_id, "dashboard": dashboard})


def schedule_campaign(
    event_id: str,
    campaign_id: str,
    approval_due: str = "",
    publish_at: str = "",
    reminder_note: str = "",
) -> str:
    """Schedule campaign approval/publishing dates and reminder notes."""
    state = _load_state()
    event = _get_event(state, event_id)
    _get_campaign(event, campaign_id)
    item = {
        "event_id": event_id,
        "campaign_id": campaign_id,
        "approval_due": approval_due.strip(),
        "publish_at": publish_at.strip(),
        "reminder_note": reminder_note.strip() or "Review campaign approvals and publishing readiness.",
        "created_at": _now(),
        "status": "scheduled",
    }
    state.setdefault("calendar", []).append(item)
    _save_state(state)
    return _json({"ok": True, "calendar_item": item, "calendar": state["calendar"]})


def save_organization_profile(
    organization_name: str = "",
    brand_colors: str = "",
    tone: str = "",
    logo_notes: str = "",
    recurring_hashtags: str = "",
    preferred_sdgs: str = "",
) -> str:
    """Save reusable organization memory for brand, tone, hashtags, and preferred SDGs."""
    state = _load_state()
    profile = state.setdefault("organization_profile", {})
    if organization_name:
        profile["organization_name"] = organization_name.strip()
    if brand_colors:
        profile["brand_colors"] = _split_csv(brand_colors)
    if tone:
        profile["tone"] = tone.strip()
    if logo_notes:
        profile["logo_notes"] = logo_notes.strip()
    if recurring_hashtags:
        profile["recurring_hashtags"] = _extract_hashtags(recurring_hashtags)
    if preferred_sdgs:
        profile["preferred_sdgs"] = [
            int(value) for value in re.findall(r"\d+", preferred_sdgs) if 1 <= int(value) <= 17
        ]
    profile["updated_at"] = _now()
    _save_state(state)
    return _json({"ok": True, "organization_profile": profile})


def save_partner_memory(
    partner_name: str,
    partner_type: str = "",
    wording_notes: str = "",
    logo_usage: str = "",
) -> str:
    """Save partner/sponsor wording and logo usage preferences."""
    state = _load_state()
    partners = state.setdefault("partners", [])
    partner = {
        "partner_name": partner_name.strip(),
        "partner_type": partner_type.strip(),
        "wording_notes": wording_notes.strip(),
        "logo_usage": logo_usage.strip(),
        "updated_at": _now(),
    }
    partners = [item for item in partners if item.get("partner_name", "").lower() != partner_name.strip().lower()]
    partners.append(partner)
    state["partners"] = partners
    _save_state(state)
    return _json({"ok": True, "partner": partner, "partners": partners})


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
