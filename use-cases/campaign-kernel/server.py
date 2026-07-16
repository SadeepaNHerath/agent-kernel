from __future__ import annotations

import json
import mimetypes
import os
import re
import shlex
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from agentkernel.api import RESTAPI
from agentkernel.openai import OpenAIModule
from agentkernel.telegram import AgentTelegramRequestHandler
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from agent import AGENTS
from telegram_ux import (
    campaign_approved_message,
    campaign_keyboard,
    campaign_pack_message,
    caption_approved_message,
    caption_keyboard,
    caption_pack_message,
    content_approved_keyboard,
    content_approved_message,
    content_draft_message,
    content_keyboard,
    context_updated_message,
    dashboard_message,
    event_created_message,
    event_keyboard,
    flyer_approved_keyboard,
    flyer_approved_message,
    flyer_keyboard,
    flyer_photo_caption,
    help_message,
    impact_message,
    load_payload,
    parse_callback_data,
    publish_keyboard,
    publish_results_message,
    report_message,
    sample_saved_message,
    start_message,
    status_message,
)
from tool import (
    analyze_sample_flyer_context,
    approve_campaign_package,
    approve_caption_pack,
    approve_flyer,
    approve_flyer_content,
    assign_approval_roles,
    create_event_context,
    draft_flyer_content,
    edit_caption_pack,
    edit_flyer_content,
    enrich_campaign_intelligence,
    generate_campaign_impact_report,
    generate_caption_pack,
    generate_flyer,
    generate_one_click_campaign_pack,
    get_campaign_status,
    get_event_context,
    get_impact_dashboard,
    ingest_direct_campaign_assets,
    publish_campaign,
    save_organization_profile,
    save_partner_memory,
    schedule_campaign,
    update_event_context,
)

OpenAIModule(AGENTS)

CHAT_STATE: dict[Any, dict[str, str]] = {}
PROCESSED_UPDATE_IDS: list[int] = []
STATE_DIR_ENV = "CAMPAIGN_KERNEL_STATE_DIR"
DEFAULT_STATE_DIR = ".campaign_kernel_state"
CHAT_STATE_FILE = "chat_state.json"
PROCESSED_UPDATES_FILE = "processed_updates.json"
MAX_PROCESSED_UPDATES = 500
MAX_UPLOAD_MB_ENV = "CAMPAIGN_KERNEL_MAX_UPLOAD_MB"
DEFAULT_MAX_UPLOAD_MB = 12
ALLOWED_UPLOAD_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
FALSE_VALUES = {"0", "false", "no", "off"}
WEB_DIR = Path(__file__).parent / "web"

TELEGRAM_COMMANDS = [
    {"command": "start", "description": "Start CampaignKernel"},
    {"command": "new_event", "description": "Create a reusable event workspace"},
    {"command": "context", "description": "Add theme, colors, logos, or caption style"},
    {"command": "brief", "description": "Draft campaign content from a short brief"},
    {"command": "pack", "description": "Generate flyer, captions, SDGs, and impact pack"},
    {"command": "impact", "description": "Show SDG alignment, goals, and quality score"},
    {"command": "report", "description": "Create a judge-ready impact report"},
    {"command": "dashboard", "description": "Show impact dashboard"},
    {"command": "roles", "description": "Assign organizer, designer, editor, approver"},
    {"command": "status", "description": "Show current event or campaign status"},
    {"command": "help", "description": "Show the simple workflow"},
]


def _parts(command: str) -> tuple[str, list[str]]:
    pieces = shlex.split(command)
    if not pieces:
        return "", []
    return pieces[0].split("@", 1)[0].lower(), pieces[1:]


def _arg(args: list[str], index: int, default: str = "") -> str:
    return args[index] if index < len(args) else default


def _looks_like_event_id(value: str) -> bool:
    return value.lower().startswith("ck-")


def _looks_like_campaign_id(value: str) -> bool:
    return bool(re.fullmatch(r"CK-\d{4,}", value.strip(), flags=re.IGNORECASE))


def _active(chat_id: int) -> dict[str, str]:
    if chat_id in CHAT_STATE:
        return CHAT_STATE.setdefault(chat_id, {})
    return CHAT_STATE.setdefault(str(chat_id), {})


def _remember_chat(chat_id: int, event_id: str = "", campaign_id: str = "") -> None:
    active = _active(chat_id)
    if event_id:
        active["event_id"] = event_id
    if campaign_id:
        active["campaign_id"] = campaign_id
    active["updated_at"] = _now()
    _save_chat_state()


def _resolve_event_and_rest(chat_id: int, args: list[str]) -> tuple[str, list[str]]:
    if args and _looks_like_event_id(args[0]):
        return args[0], args[1:]
    return _active(chat_id).get("event_id", ""), args


def _resolve_ids(chat_id: int, args: list[str]) -> tuple[str, str, list[str]]:
    rest = list(args)
    event_id = _active(chat_id).get("event_id", "")
    campaign_id = _active(chat_id).get("campaign_id", "")
    if rest and _looks_like_event_id(rest[0]):
        event_id = rest.pop(0)
    if rest and _looks_like_campaign_id(rest[0]):
        campaign_id = rest.pop(0)
    return event_id, campaign_id, rest


def _extract_labeled_value(raw: str, label: str) -> str:
    marker = f"{label}="
    if marker not in raw:
        return ""
    value = raw.split(marker, 1)[1]
    labels = [
        "name=",
        "theme=",
        "colors=",
        "tone=",
        "logo=",
        "asset=",
        "flyer=",
        "sample_caption=",
        "caption=",
        "style_analysis=",
        "sdgs=",
        "type=",
        "wording=",
        "organizer=",
        "designer=",
        "editor=",
        "final_approver=",
        "approval_due=",
        "publish_at=",
        "note=",
    ]
    for next_label in labels:
        if next_label != marker and next_label in value:
            value = value.split(next_label, 1)[0]
    return value.strip(" ;")


def _context_kwargs(notes: str) -> dict[str, str]:
    return {
        "theme_notes": _extract_labeled_value(notes, "theme") or notes,
        "colors": _extract_labeled_value(notes, "colors"),
        "logo_notes": _extract_labeled_value(notes, "logo"),
        "asset_ref": _extract_labeled_value(notes, "asset"),
        "sample_flyer_notes": _extract_labeled_value(notes, "flyer"),
        "sample_caption": _extract_labeled_value(notes, "sample_caption"),
        "style_analysis": _extract_labeled_value(notes, "style_analysis"),
        "caption_structure": _extract_labeled_value(notes, "caption"),
        "default_hashtags": notes,
    }


def _read_web_file(name: str) -> str:
    path = WEB_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Web asset not found.")
    return path.read_text(encoding="utf-8")


async def _request_json(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _state_dir() -> Path:
    return Path(os.environ.get(STATE_DIR_ENV, DEFAULT_STATE_DIR))


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_path(name: str) -> Path:
    return _state_dir() / name


def _state_root() -> Path:
    root = _state_dir()
    if not root.is_absolute():
        root = Path.cwd() / root
    return root.resolve()


def _safe_artifact_path(raw_path: str) -> Path:
    if not raw_path:
        raise HTTPException(status_code=400, detail="Missing artifact path.")
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    resolved = path.resolve()
    state_root = _state_root()
    if resolved != state_root and state_root not in resolved.parents:
        raise HTTPException(status_code=403, detail="Artifact path is outside CampaignKernel state.")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return resolved


def _read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
    temp_path.replace(path)


def _load_chat_state() -> dict[str, dict[str, str]]:
    data = _read_json_file(_json_path(CHAT_STATE_FILE), {})
    return data if isinstance(data, dict) else {}


def _save_chat_state() -> None:
    serializable = {str(chat_id): value for chat_id, value in CHAT_STATE.items() if isinstance(value, dict)}
    _write_json_file(_json_path(CHAT_STATE_FILE), serializable)


def _load_processed_update_ids() -> list[int]:
    data = _read_json_file(_json_path(PROCESSED_UPDATES_FILE), [])
    if not isinstance(data, list):
        return []
    update_ids = []
    for update_id in data[-MAX_PROCESSED_UPDATES:]:
        try:
            update_ids.append(int(update_id))
        except (TypeError, ValueError):
            continue
    return update_ids


def _save_processed_update_ids() -> None:
    _write_json_file(_json_path(PROCESSED_UPDATES_FILE), PROCESSED_UPDATE_IDS[-MAX_PROCESSED_UPDATES:])


def _was_update_processed(update_id: int | None) -> bool:
    return update_id is not None and update_id in PROCESSED_UPDATE_IDS


def _remember_processed_update(update_id: int | None) -> None:
    if update_id is None or update_id in PROCESSED_UPDATE_IDS:
        return
    PROCESSED_UPDATE_IDS.append(update_id)
    del PROCESSED_UPDATE_IDS[:-MAX_PROCESSED_UPDATES]
    _save_processed_update_ids()


def _max_upload_bytes() -> int:
    raw_value = os.environ.get(MAX_UPLOAD_MB_ENV, str(DEFAULT_MAX_UPLOAD_MB))
    try:
        megabytes = max(1, int(raw_value))
    except ValueError:
        megabytes = DEFAULT_MAX_UPLOAD_MB
    return megabytes * 1024 * 1024


def _safe_file_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip(".-")
    return cleaned or "sample-flyer"


def _is_allowed_upload(file_name: str, telegram_path: str = "", mime_type: str = "") -> bool:
    suffix = Path(file_name or telegram_path).suffix.lower()
    if suffix in ALLOWED_UPLOAD_SUFFIXES:
        return True
    return mime_type.startswith("image/") or mime_type == "application/pdf"


def _state_dir_writable() -> bool:
    try:
        path = _state_dir()
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _runtime_diagnostics() -> dict[str, Any]:
    return {
        "ok": True,
        "checked_at": _now(),
        "state_dir": str(_state_dir()),
        "state_dir_writable": _state_dir_writable(),
        "telegram_token_configured": bool(os.environ.get("AK_TELEGRAM__BOT_TOKEN")),
        "webhook_secret_configured": bool(os.environ.get("AK_TELEGRAM__WEBHOOK_SECRET")),
        "openai_key_configured": bool(os.environ.get("OPENAI_API_KEY")),
        "openai_base_url_configured": bool(os.environ.get("OPENAI_BASE_URL")),
        "openai_model": os.environ.get("OPENAI_MODEL", ""),
        "mock_publish": os.environ.get("CAMPAIGN_KERNEL_MOCK_PUBLISH", "true"),
        "live_publish": os.environ.get("CAMPAIGN_KERNEL_LIVE_PUBLISH", "false"),
        "image_mode": os.environ.get("CAMPAIGN_KERNEL_IMAGE_MODE", "template"),
        "max_upload_mb": _max_upload_bytes() // (1024 * 1024),
        "active_chats": len(CHAT_STATE),
        "processed_update_cache": len(PROCESSED_UPDATE_IDS),
    }


def _log_runtime_warnings(log: Any) -> None:
    diagnostics = _runtime_diagnostics()
    required_flags = {
        "AK_TELEGRAM__BOT_TOKEN": diagnostics["telegram_token_configured"],
        "OPENAI_API_KEY": diagnostics["openai_key_configured"],
    }
    for name, configured in required_flags.items():
        if not configured:
            log.warning("%s is not configured.", name)
    if not diagnostics["webhook_secret_configured"]:
        log.warning("AK_TELEGRAM__WEBHOOK_SECRET is not configured; webhook requests are less protected.")
    if not diagnostics["state_dir_writable"]:
        log.warning("CampaignKernel state directory is not writable: %s", diagnostics["state_dir"])


CHAT_STATE.update(_load_chat_state())
PROCESSED_UPDATE_IDS.extend(_load_processed_update_ids())


def _sample_event_id(text: str, chat_id: int) -> str:
    match = re.search(r"\bsample\s+for\s+(ck-[a-z0-9-]+)", text, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    event_match = re.search(r"\b(ck-[a-z0-9-]+)\b", text, flags=re.IGNORECASE)
    if event_match:
        return event_match.group(1)
    if text.lower().strip() in {"sample", "sample flyer", "sample for event"}:
        return _active(chat_id).get("event_id", "")
    return ""


def _upload_event_id(text: str, chat_id: int) -> str:
    return _sample_event_id(text, chat_id) or _active(chat_id).get("event_id", "")


class CampaignTelegramHandler(AgentTelegramRequestHandler):
    def __init__(self):
        super().__init__()
        self._commands_registered = False
        _log_runtime_warnings(self._log)
        if os.environ.get("CAMPAIGN_KERNEL_REGISTER_COMMANDS", "true").lower() not in FALSE_VALUES:
            threading.Thread(target=self._set_my_commands_sync, daemon=True).start()

    def get_router(self):
        router = super().get_router()

        @router.get("/campaign/ready")
        def campaign_ready():
            diagnostics = _runtime_diagnostics()
            diagnostics["commands_registered"] = self._commands_registered
            diagnostics["ready"] = (
                diagnostics["state_dir_writable"]
                and diagnostics["telegram_token_configured"]
                and diagnostics["openai_key_configured"]
            )
            return diagnostics

        @router.get("/campaign/diagnostics")
        def campaign_diagnostics():
            diagnostics = _runtime_diagnostics()
            diagnostics["commands_registered"] = self._commands_registered
            diagnostics["safe_note"] = "No secrets are returned by this endpoint."
            return diagnostics

        @router.get("/", response_class=HTMLResponse)
        def campaign_workspace():
            return HTMLResponse(_read_web_file("index.html"))

        @router.get("/campaign/docs", response_class=HTMLResponse)
        def campaign_docs():
            return HTMLResponse(_read_web_file("docs.html"))

        @router.get("/web/{asset_name}")
        def campaign_web_asset(asset_name: str):
            if asset_name not in {"app.js", "styles.css"}:
                raise HTTPException(status_code=404, detail="Web asset not found.")
            path = WEB_DIR / asset_name
            return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0])

        @router.get("/api/dashboard")
        def web_dashboard(event_id: str = ""):
            return JSONResponse(load_payload(get_impact_dashboard(event_id)))

        @router.post("/api/org-profile")
        async def web_org_profile(request: Request):
            body = await _request_json(request)
            result = save_organization_profile(
                organization_name=str(body.get("organization_name", "")),
                brand_colors=str(body.get("brand_colors", "")),
                tone=str(body.get("tone", "")),
                logo_notes=str(body.get("logo_notes", "")),
                recurring_hashtags=str(body.get("recurring_hashtags", "")),
                preferred_sdgs=str(body.get("preferred_sdgs", "")),
            )
            return JSONResponse(load_payload(result))

        @router.post("/api/partners")
        async def web_partner(request: Request):
            body = await _request_json(request)
            result = save_partner_memory(
                partner_name=str(body.get("partner_name", "")),
                partner_type=str(body.get("partner_type", "")),
                wording_notes=str(body.get("wording_notes", "")),
                logo_usage=str(body.get("logo_usage", "")),
            )
            return JSONResponse(load_payload(result))

        @router.post("/api/quick-pack")
        async def web_quick_pack(request: Request):
            body = await _request_json(request)
            event_name = str(body.get("event_name", "")).strip() or "CampaignKernel Demo Event"
            brief = str(body.get("brief", "")).strip()
            if not brief:
                return JSONResponse(
                    {"ok": False, "blocked": True, "reason": "Campaign brief is required."}, status_code=400
                )

            event_payload = load_payload(
                create_event_context(
                    event_name,
                    theme_notes=str(body.get("theme_notes", "")),
                    tone=str(body.get("tone", "")),
                    colors=str(body.get("colors", "")),
                    default_hashtags=str(body.get("default_hashtags", "")),
                )
            )
            event_id = event_payload.get("event_id", "")
            if body.get("sample_caption") or body.get("style_notes"):
                update_event_context(
                    event_id=event_id,
                    sample_caption=str(body.get("sample_caption", "")),
                    sample_flyer_notes=str(body.get("style_notes", "")),
                    caption_structure=str(body.get("caption_structure", "")),
                    default_hashtags=str(body.get("default_hashtags", "")),
                )
            draft_payload = load_payload(draft_flyer_content(event_id=event_id, campaign_brief=brief))
            campaign_id = draft_payload.get("campaign", {}).get("campaign_id", "")
            if not campaign_id:
                return JSONResponse(draft_payload, status_code=400)
            pack_payload = load_payload(
                generate_one_click_campaign_pack(
                    event_id=event_id,
                    campaign_id=campaign_id,
                    design_instruction=str(body.get("design_instruction", "")),
                    caption_direction=str(body.get("caption_direction", "")),
                )
            )
            if pack_payload.get("ok"):
                report_payload = load_payload(generate_campaign_impact_report(event_id, campaign_id))
                pack_payload["impact_report"] = {
                    "path": report_payload.get("path", ""),
                    "artifact_url": f"/api/artifact?path={report_payload.get('path', '')}",
                }
                flyer_path = pack_payload.get("flyer", {}).get("path", "")
                if flyer_path:
                    pack_payload["flyer"]["artifact_url"] = f"/api/artifact?path={flyer_path}"
            return JSONResponse(pack_payload, status_code=200 if pack_payload.get("ok") else 400)

        @router.post("/api/campaigns/{event_id}")
        async def web_create_campaign(event_id: str, request: Request):
            body = await _request_json(request)
            brief = str(body.get("brief", "")).strip()
            if not brief:
                return JSONResponse(
                    {"ok": False, "blocked": True, "reason": "Campaign brief is required."}, status_code=400
                )
            result = draft_flyer_content(event_id=event_id, campaign_brief=brief)
            return JSONResponse(load_payload(result))

        @router.post("/api/campaigns/{event_id}/{campaign_id}/pack")
        async def web_generate_pack(event_id: str, campaign_id: str, request: Request):
            body = await _request_json(request)
            result = load_payload(
                generate_one_click_campaign_pack(
                    event_id=event_id,
                    campaign_id=campaign_id,
                    design_instruction=str(body.get("design_instruction", "")),
                    caption_direction=str(body.get("caption_direction", "")),
                )
            )
            if result.get("ok") and result.get("flyer", {}).get("path"):
                result["flyer"]["artifact_url"] = f"/api/artifact?path={result['flyer']['path']}"
            return JSONResponse(result, status_code=200 if result.get("ok") else 400)

        @router.get("/api/campaigns/{event_id}/{campaign_id}")
        def web_campaign_status(event_id: str, campaign_id: str):
            return JSONResponse(load_payload(get_campaign_status(event_id, campaign_id)))

        @router.post("/api/campaigns/{event_id}/{campaign_id}/impact")
        def web_campaign_impact(event_id: str, campaign_id: str):
            return JSONResponse(load_payload(enrich_campaign_intelligence(event_id, campaign_id)))

        @router.post("/api/campaigns/{event_id}/{campaign_id}/report")
        def web_campaign_report(event_id: str, campaign_id: str):
            result = load_payload(generate_campaign_impact_report(event_id, campaign_id))
            if result.get("path"):
                result["artifact_url"] = f"/api/artifact?path={result['path']}"
            return JSONResponse(result)

        @router.post("/api/campaigns/{event_id}/{campaign_id}/schedule")
        async def web_schedule_campaign(event_id: str, campaign_id: str, request: Request):
            body = await _request_json(request)
            result = schedule_campaign(
                event_id,
                campaign_id,
                approval_due=str(body.get("approval_due", "")),
                publish_at=str(body.get("publish_at", "")),
                reminder_note=str(body.get("reminder_note", "")),
            )
            return JSONResponse(load_payload(result))

        @router.post("/api/campaigns/{event_id}/{campaign_id}/roles")
        async def web_campaign_roles(event_id: str, campaign_id: str, request: Request):
            body = await _request_json(request)
            result = assign_approval_roles(
                event_id,
                campaign_id,
                organizer=str(body.get("organizer", "")),
                designer=str(body.get("designer", "")),
                editor=str(body.get("editor", "")),
                final_approver=str(body.get("final_approver", "")),
            )
            return JSONResponse(load_payload(result))

        @router.get("/api/artifact")
        def web_artifact(path: str):
            artifact = _safe_artifact_path(path)
            return FileResponse(artifact, media_type=mimetypes.guess_type(artifact.name)[0])

        return router

    def _set_my_commands_sync(self) -> None:
        if self._commands_registered:
            return
        url = f"{self._base_url}/setMyCommands"
        try:
            with httpx.Client(timeout=self._http_timeout) as client:
                response = client.post(url, json={"commands": TELEGRAM_COMMANDS})
                response.raise_for_status()
                self._commands_registered = True
                self._log.info("CampaignKernel Telegram commands registered.")
        except Exception as error:
            self._log.warning("Could not register Telegram commands: %s", error)

    async def _set_my_commands(self) -> None:
        if self._commands_registered:
            return
        url = f"{self._base_url}/setMyCommands"
        try:
            async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                response = await client.post(url, json={"commands": TELEGRAM_COMMANDS})
                response.raise_for_status()
                self._commands_registered = True
                self._log.info("CampaignKernel Telegram commands registered.")
        except Exception as error:
            self._log.warning("Could not register Telegram commands: %s", error)

    async def _process_webhook_body(self, body: dict):
        update_id = body.get("update_id")
        if _was_update_processed(update_id):
            self._log.info("Skipping duplicate Telegram update: %s", update_id)
            return
        try:
            self._log.debug("Received CampaignKernel Telegram update: %s", body)
            if "message" in body:
                await self._handle_message(body["message"])
            elif "edited_message" in body:
                await self._handle_message(body["edited_message"])
            elif "channel_post" in body:
                await self._handle_message(body["channel_post"])
            elif "edited_channel_post" in body:
                await self._handle_message(body["edited_channel_post"])
            elif "callback_query" in body:
                await self._handle_callback_query(body["callback_query"])
            else:
                self._log.debug("Unhandled CampaignKernel update type: %s", list(body.keys()))
        except Exception as error:
            self._log.error("Error processing CampaignKernel Telegram update: %s", error, exc_info=True)
        finally:
            _remember_processed_update(update_id)

    async def _send_photo(
        self, chat_id: int, photo_path: str, caption: str, reply_markup: dict[str, Any] | None = None
    ):
        path = Path(photo_path)
        if not path.exists():
            await self._send_message(chat_id, f"{caption}\n\nI could not find the flyer file at {photo_path}.")
            return

        await self._send_chat_action(chat_id, "upload_photo")
        url = f"{self._base_url}/sendPhoto"
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
        data: dict[str, str] = {"chat_id": str(chat_id), "caption": caption}
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)

        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            with path.open("rb") as handle:
                files = {"photo": (path.name, handle, mime_type)}
                response = await client.post(url, data=data, files=files)
                response.raise_for_status()

    async def _send_flyer_result(self, chat_id: int, payload: str) -> None:
        data = load_payload(payload)
        event_id = data.get("event_id", "")
        campaign_id = data.get("campaign_id", "")
        if event_id or campaign_id:
            _remember_chat(chat_id, event_id, campaign_id)
        if not data.get("ok"):
            await self._send_message(chat_id, flyer_photo_caption(data))
            return

        flyer = data.get("flyer", {})
        await self._send_photo(
            chat_id, flyer.get("path", ""), flyer_photo_caption(data), flyer_keyboard(event_id, campaign_id)
        )

    async def _send_pack_result(self, chat_id: int, payload: str) -> None:
        data = load_payload(payload)
        event_id = data.get("event_id", "")
        campaign_id = data.get("campaign_id", "")
        if event_id or campaign_id:
            _remember_chat(chat_id, event_id, campaign_id)
        if not data.get("ok"):
            await self._send_message(chat_id, campaign_pack_message(data))
            return

        flyer = data.get("flyer", {})
        await self._send_photo(
            chat_id,
            flyer.get("path", ""),
            campaign_pack_message(data),
            caption_keyboard(event_id, campaign_id),
        )
        await self._send_message(
            chat_id,
            caption_pack_message({"ok": True, "caption_pack": data.get("caption_pack", {})}),
            reply_markup=caption_keyboard(event_id, campaign_id),
        )
        await self._send_message(
            chat_id,
            impact_message({"ok": True, "intelligence": data.get("intelligence", {})}),
            reply_markup=campaign_keyboard(event_id, campaign_id),
        )

    async def _save_uploaded_sample(self, event_id: str, message: dict[str, Any]) -> tuple[str, str]:
        file_id = ""
        file_name = "sample-flyer.jpg"
        mime_type = ""
        if "photo" in message and message.get("photo"):
            file_id = message["photo"][-1].get("file_id", "")
        elif "document" in message:
            document = message.get("document", {})
            file_id = document.get("file_id", "")
            file_name = document.get("file_name", "sample-flyer")
            mime_type = document.get("mime_type", "")

        if not file_id:
            raise ValueError("No image or document file was found.")

        file_info = await self._get_file_info(file_id)
        if not file_info or not file_info.get("file_path"):
            raise ValueError("Telegram did not return a downloadable file path.")

        telegram_path = file_info["file_path"]
        if "photo" in message:
            file_name = Path(telegram_path).name or file_name
        file_size = file_info.get("file_size", 0)
        max_upload_bytes = _max_upload_bytes()
        if isinstance(file_size, int) and file_size > max_upload_bytes:
            raise ValueError(
                f"Sample file is too large. Maximum allowed size is {max_upload_bytes // (1024 * 1024)} MB."
            )
        if not _is_allowed_upload(file_name, telegram_path, mime_type):
            raise ValueError("Please upload a PNG, JPG, WEBP, or PDF sample flyer.")

        content = await self._download_telegram_file(telegram_path)
        if content is None:
            raise ValueError("The sample file could not be downloaded.")
        if len(content) > max_upload_bytes:
            raise ValueError(
                f"Sample file is too large. Maximum allowed size is {max_upload_bytes // (1024 * 1024)} MB."
            )

        upload_dir = _state_dir() / "uploads" / event_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / _safe_file_name(file_name)
        if target.exists():
            target = upload_dir / f"{target.stem}-{message.get('message_id', 'new')}{target.suffix}"
        target.write_bytes(content)
        return file_name, str(target)

    async def _handle_message(self, message: dict):
        chat_id = message.get("chat", {}).get("id")
        text = (message.get("text") or message.get("caption") or "").strip()
        has_upload = "photo" in message or "document" in message

        if chat_id and has_upload:
            event_id = _upload_event_id(text, chat_id)
            if event_id:
                try:
                    await self._send_chat_action(chat_id, "typing")
                    file_name, file_path = await self._save_uploaded_sample(event_id, message)
                    result = analyze_sample_flyer_context(
                        event_id=event_id,
                        file_name=file_name,
                        caption=text,
                        file_path=file_path,
                    )
                    _remember_chat(chat_id, event_id)
                    await self._send_message(
                        chat_id, sample_saved_message(result), reply_markup=event_keyboard(event_id)
                    )
                except Exception as error:
                    await self._send_message(chat_id, f"I could not save that sample flyer. {error}")
                return
            await self._send_message(
                chat_id,
                "I received the file, but no event is active yet.\nCreate one with /new_event Event Name, then upload the sample again.",
            )
            return

        await super()._handle_message(message)

    async def _handle_callback_query(self, callback_query: dict):
        query_id = callback_query.get("id")
        data = callback_query.get("data", "")
        message = callback_query.get("message", {})
        chat_id = message.get("chat", {}).get("id")

        await self._answer_callback_query(query_id, "Working...")
        if not chat_id or not data:
            return

        try:
            callback = parse_callback_data(data)
            event_id = callback.event_id or _active(chat_id).get("event_id", "")
            campaign_id = callback.campaign_id or _active(chat_id).get("campaign_id", "")
            if event_id or campaign_id:
                _remember_chat(chat_id, event_id, campaign_id)

            if callback.action == "edit_content":
                await self._send_message(
                    chat_id,
                    f"Send your edits like this:\n/edit {event_id} {campaign_id} date=July 25; venue=Main Hall; cta=Register now",
                )
                return

            if callback.action == "edit_flyer":
                await self._send_message(
                    chat_id,
                    f"Send flyer changes like this:\n/generate_flyer {event_id} {campaign_id} cleaner layout, larger CTA",
                )
                return

            if callback.action == "edit_caption":
                await self._send_message(
                    chat_id,
                    f"Send caption edits like this:\n/edit {event_id} {campaign_id} make it shorter and more energetic",
                )
                return

            if callback.action == "approve_content":
                result = approve_flyer_content(event_id, campaign_id)
                await self._send_message(
                    chat_id,
                    content_approved_message(result),
                    reply_markup=content_approved_keyboard(event_id, campaign_id),
                )
                return

            if callback.action == "one_click_pack":
                await self._send_pack_result(
                    chat_id,
                    generate_one_click_campaign_pack(
                        event_id,
                        campaign_id,
                        "professional SDG campaign pack from the saved event style",
                        "clear, local, approval-ready",
                    ),
                )
                return

            if callback.action == "generate_flyer":
                await self._send_flyer_result(chat_id, generate_flyer(event_id, campaign_id))
                return

            if callback.action == "regenerate_flyer":
                await self._send_flyer_result(
                    chat_id, generate_flyer(event_id, campaign_id, "Regenerated from latest approved content.")
                )
                return

            if callback.action == "approve_flyer":
                result = approve_flyer(event_id, campaign_id)
                await self._send_message(
                    chat_id, flyer_approved_message(result), reply_markup=flyer_approved_keyboard(event_id, campaign_id)
                )
                return

            if callback.action == "generate_caption":
                result = generate_caption_pack(event_id, campaign_id)
                await self._send_message(
                    chat_id, caption_pack_message(result), reply_markup=caption_keyboard(event_id, campaign_id)
                )
                return

            if callback.action == "regenerate_caption":
                result = generate_caption_pack(event_id, campaign_id, "Regenerate in the saved event style.")
                await self._send_message(
                    chat_id, caption_pack_message(result), reply_markup=caption_keyboard(event_id, campaign_id)
                )
                return

            if callback.action == "approve_caption":
                result = approve_caption_pack(event_id, campaign_id)
                await self._send_message(
                    chat_id, caption_approved_message(result), reply_markup=campaign_keyboard(event_id, campaign_id)
                )
                return

            if callback.action == "approve_campaign":
                result = approve_campaign_package(event_id, campaign_id)
                await self._send_message(
                    chat_id, campaign_approved_message(result), reply_markup=publish_keyboard(event_id, campaign_id)
                )
                return

            if callback.action == "impact":
                result = enrich_campaign_intelligence(event_id, campaign_id)
                await self._send_message(
                    chat_id, impact_message(result), reply_markup=campaign_keyboard(event_id, campaign_id)
                )
                return

            if callback.action == "report":
                result = generate_campaign_impact_report(event_id, campaign_id)
                await self._send_message(
                    chat_id, report_message(result), reply_markup=campaign_keyboard(event_id, campaign_id)
                )
                return

            if callback.action == "dashboard":
                result = get_impact_dashboard(event_id)
                await self._send_message(chat_id, dashboard_message(result))
                return

            if callback.action in {"publish", "export"}:
                target_map = {"social": "instagram facebook linkedin", "wa": "whatsapp"}
                targets = target_map.get(callback.value, callback.value.replace(",", " "))
                if not targets and callback.action == "export":
                    targets = "whatsapp"
                result = publish_campaign(event_id, campaign_id, targets)
                await self._send_message(chat_id, publish_results_message(result))
                return

            if callback.action == "status":
                result = get_campaign_status(event_id, campaign_id)
                await self._send_message(chat_id, status_message(result))
                return

            await self._send_message(chat_id, "I do not recognize that button action yet.")
        except Exception as error:
            await self._send_message(chat_id, f"That button action failed: {error}")

    async def _handle_command(self, chat_id: int, command: str):
        cmd, args = _parts(command)

        try:
            if cmd == "/start":
                await self._send_message(chat_id, start_message())
                return

            if cmd == "/help":
                await self._send_message(chat_id, help_message())
                return

            if cmd == "/new_event":
                event_name = " ".join(args).strip()
                if not event_name:
                    await self._send_message(chat_id, "Send it like this:\n/new_event IDEALIZE AI Workshop")
                    return
                result = create_event_context(event_name)
                data = load_payload(result)
                _remember_chat(chat_id, data.get("event_id", ""))
                await self._send_message(
                    chat_id, event_created_message(data), reply_markup=event_keyboard(data.get("event_id", ""))
                )
                return

            if cmd in {"/context", "/upload_assets"}:
                event_id, rest = _resolve_event_and_rest(chat_id, args)
                if not event_id:
                    await self._send_message(chat_id, "Create an event first with /new_event Event Name.")
                    return
                notes = " ".join(rest)
                result = update_event_context(event_id=event_id, **_context_kwargs(notes))
                _remember_chat(chat_id, event_id)
                await self._send_message(
                    chat_id, context_updated_message(result), reply_markup=event_keyboard(event_id)
                )
                return

            if cmd == "/style_summary":
                event_id, _rest = _resolve_event_and_rest(chat_id, args)
                if not event_id:
                    await self._send_message(chat_id, "Create or select an event first.")
                    return
                await self._send_message(
                    chat_id, status_message(get_campaign_status(event_id)), reply_markup=event_keyboard(event_id)
                )
                return

            if cmd == "/debug_status":
                event_id, campaign_id, _rest = _resolve_ids(chat_id, args)
                await self._send_message(chat_id, get_campaign_status(event_id, campaign_id))
                return

            if cmd == "/brief":
                event_id, rest = _resolve_event_and_rest(chat_id, args)
                brief = " ".join(rest).strip()
                if not event_id:
                    await self._send_message(chat_id, "Create an event first with /new_event Event Name.")
                    return
                if not brief:
                    await self._send_message(
                        chat_id,
                        "Send the brief like this:\n/brief Free AI workshop on July 25 at UoM. Register via link in bio.",
                    )
                    return
                result = draft_flyer_content(event_id=event_id, campaign_brief=brief)
                data = load_payload(result)
                campaign_id = data.get("campaign", {}).get("campaign_id", "")
                _remember_chat(chat_id, event_id, campaign_id)
                await self._send_message(
                    chat_id, content_draft_message(data), reply_markup=content_keyboard(event_id, campaign_id)
                )
                return

            if cmd == "/edit":
                event_id, campaign_id, rest = _resolve_ids(chat_id, args)
                instruction = " ".join(rest).strip()
                if not event_id or not campaign_id or not instruction:
                    await self._send_message(
                        chat_id, "Send edits like this:\n/edit date=July 25; venue=Main Hall; cta=Register now"
                    )
                    return
                status = get_campaign_status(event_id=event_id, campaign_id=campaign_id)
                if '"status": "caption_draft"' in status or '"status": "caption_approved"' in status:
                    result = edit_caption_pack(event_id, campaign_id, instruction)
                    await self._send_message(
                        chat_id, caption_pack_message(result), reply_markup=caption_keyboard(event_id, campaign_id)
                    )
                else:
                    result = edit_flyer_content(event_id, campaign_id, instruction)
                    await self._send_message(
                        chat_id, content_draft_message(result), reply_markup=content_keyboard(event_id, campaign_id)
                    )
                return

            if cmd == "/approve_content":
                event_id, campaign_id, _rest = _resolve_ids(chat_id, args)
                result = approve_flyer_content(event_id, campaign_id)
                await self._send_message(
                    chat_id,
                    content_approved_message(result),
                    reply_markup=content_approved_keyboard(event_id, campaign_id),
                )
                return

            if cmd == "/generate_flyer":
                event_id, campaign_id, rest = _resolve_ids(chat_id, args)
                await self._send_flyer_result(chat_id, generate_flyer(event_id, campaign_id, " ".join(rest)))
                return

            if cmd == "/pack":
                event_id, campaign_id, rest = _resolve_ids(chat_id, args)
                if not event_id or not campaign_id:
                    await self._send_message(chat_id, "Create a brief first, then send /pack.")
                    return
                direction = " ".join(rest).strip() or "professional SDG campaign pack from the saved event style"
                await self._send_pack_result(
                    chat_id,
                    generate_one_click_campaign_pack(
                        event_id,
                        campaign_id,
                        design_instruction=direction,
                        caption_direction=direction,
                    ),
                )
                return

            if cmd == "/approve_flyer":
                event_id, campaign_id, _rest = _resolve_ids(chat_id, args)
                result = approve_flyer(event_id, campaign_id)
                await self._send_message(
                    chat_id, flyer_approved_message(result), reply_markup=flyer_approved_keyboard(event_id, campaign_id)
                )
                return

            if cmd == "/caption":
                event_id, campaign_id, rest = _resolve_ids(chat_id, args)
                result = generate_caption_pack(event_id, campaign_id, " ".join(rest))
                await self._send_message(
                    chat_id, caption_pack_message(result), reply_markup=caption_keyboard(event_id, campaign_id)
                )
                return

            if cmd == "/approve_caption":
                event_id, campaign_id, _rest = _resolve_ids(chat_id, args)
                result = approve_caption_pack(event_id, campaign_id)
                await self._send_message(
                    chat_id, caption_approved_message(result), reply_markup=campaign_keyboard(event_id, campaign_id)
                )
                return

            if cmd == "/approve_campaign":
                event_id, campaign_id, _rest = _resolve_ids(chat_id, args)
                result = approve_campaign_package(event_id, campaign_id)
                await self._send_message(
                    chat_id, campaign_approved_message(result), reply_markup=publish_keyboard(event_id, campaign_id)
                )
                return

            if cmd == "/impact":
                event_id, campaign_id, _rest = _resolve_ids(chat_id, args)
                if not event_id or not campaign_id:
                    await self._send_message(chat_id, "Select a campaign first, then send /impact.")
                    return
                result = enrich_campaign_intelligence(event_id, campaign_id)
                await self._send_message(
                    chat_id, impact_message(result), reply_markup=campaign_keyboard(event_id, campaign_id)
                )
                return

            if cmd == "/report":
                event_id, campaign_id, _rest = _resolve_ids(chat_id, args)
                if not event_id or not campaign_id:
                    await self._send_message(chat_id, "Select a campaign first, then send /report.")
                    return
                result = generate_campaign_impact_report(event_id, campaign_id)
                await self._send_message(
                    chat_id, report_message(result), reply_markup=campaign_keyboard(event_id, campaign_id)
                )
                return

            if cmd == "/dashboard":
                event_id, _campaign_id, _rest = _resolve_ids(chat_id, args)
                result = get_impact_dashboard(event_id)
                await self._send_message(chat_id, dashboard_message(result))
                return

            if cmd == "/schedule":
                event_id, campaign_id, rest = _resolve_ids(chat_id, args)
                raw = " ".join(rest)
                result = schedule_campaign(
                    event_id,
                    campaign_id,
                    approval_due=_extract_labeled_value(raw, "approval_due"),
                    publish_at=_extract_labeled_value(raw, "publish_at"),
                    reminder_note=_extract_labeled_value(raw, "note") or raw,
                )
                await self._send_message(
                    chat_id, "Campaign scheduled.\n" + status_message(get_campaign_status(event_id, campaign_id))
                )
                if not load_payload(result).get("ok"):
                    await self._send_message(chat_id, result)
                return

            if cmd == "/roles":
                event_id, campaign_id, rest = _resolve_ids(chat_id, args)
                raw = " ".join(rest)
                result = assign_approval_roles(
                    event_id,
                    campaign_id,
                    organizer=_extract_labeled_value(raw, "organizer"),
                    designer=_extract_labeled_value(raw, "designer"),
                    editor=_extract_labeled_value(raw, "editor"),
                    final_approver=_extract_labeled_value(raw, "final_approver"),
                )
                data = load_payload(result)
                if not data.get("ok"):
                    await self._send_message(chat_id, "I could not save approval roles. " + result)
                    return
                roles = data.get("approval_workflow", {}).get("roles", {})
                await self._send_message(
                    chat_id,
                    "\n".join(
                        [
                            "Approval roles saved.",
                            f"Organizer: {roles.get('organizer') or 'not assigned'}",
                            f"Designer: {roles.get('designer') or 'not assigned'}",
                            f"Editor: {roles.get('editor') or 'not assigned'}",
                            f"Final approver: {roles.get('final_approver') or 'not assigned'}",
                        ]
                    ),
                )
                return

            if cmd == "/org_profile":
                raw = " ".join(args)
                result = save_organization_profile(
                    organization_name=_extract_labeled_value(raw, "name"),
                    brand_colors=_extract_labeled_value(raw, "colors"),
                    tone=_extract_labeled_value(raw, "tone"),
                    logo_notes=_extract_labeled_value(raw, "logo"),
                    recurring_hashtags=raw,
                    preferred_sdgs=_extract_labeled_value(raw, "sdgs"),
                )
                data = load_payload(result)
                name = data.get("organization_profile", {}).get("organization_name", "Organization")
                await self._send_message(chat_id, f"{name} memory saved.")
                return

            if cmd == "/partner":
                raw = " ".join(args)
                result = save_partner_memory(
                    partner_name=_extract_labeled_value(raw, "name") or raw,
                    partner_type=_extract_labeled_value(raw, "type"),
                    wording_notes=_extract_labeled_value(raw, "wording"),
                    logo_usage=_extract_labeled_value(raw, "logo"),
                )
                data = load_payload(result)
                await self._send_message(
                    chat_id, f"Partner memory saved: {data.get('partner', {}).get('partner_name', '')}"
                )
                return

            if cmd == "/publish":
                event_id, campaign_id, rest = _resolve_ids(chat_id, args)
                result = publish_campaign(event_id, campaign_id, " ".join(rest))
                await self._send_message(chat_id, publish_results_message(result))
                return

            if cmd == "/export":
                event_id, campaign_id, rest = _resolve_ids(chat_id, args)
                result = publish_campaign(event_id, campaign_id, " ".join(rest) or "whatsapp")
                await self._send_message(chat_id, publish_results_message(result))
                return

            if cmd == "/direct":
                event_id, rest = _resolve_event_and_rest(chat_id, args)
                raw = " ".join(rest)
                flyer_path = ""
                caption = raw
                if "flyer=" in raw:
                    flyer_path = raw.split("flyer=", 1)[1].split(" caption=", 1)[0].strip()
                if "caption=" in raw:
                    caption = raw.split("caption=", 1)[1].strip()
                result = ingest_direct_campaign_assets(event_id=event_id, flyer_path=flyer_path, caption_text=caption)
                data = load_payload(result)
                campaign_id = data.get("campaign", {}).get("campaign_id", "")
                _remember_chat(chat_id, event_id, campaign_id)
                await self._send_message(
                    chat_id, content_draft_message(data), reply_markup=campaign_keyboard(event_id, campaign_id)
                )
                return

            if cmd == "/status":
                event_id, campaign_id, _rest = _resolve_ids(chat_id, args)
                if not event_id:
                    await self._send_message(chat_id, "No active event yet. Create one with /new_event Event Name.")
                    return
                await self._send_message(chat_id, status_message(get_campaign_status(event_id, campaign_id)))
                return

            if cmd == "/raw_event":
                event_id, _rest = _resolve_event_and_rest(chat_id, args)
                await self._send_message(chat_id, get_event_context(event_id))
                return

            await self._process_agent_message(chat_id, command)

        except Exception as error:
            await self._send_message(chat_id, f"CampaignKernel could not finish that step: {error}")


if __name__ == "__main__":
    RESTAPI.run([CampaignTelegramHandler()])
