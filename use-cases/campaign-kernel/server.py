from __future__ import annotations

import json
import mimetypes
import os
import re
import shlex
import threading
from pathlib import Path
from typing import Any

import httpx
from agentkernel.api import RESTAPI
from agentkernel.openai import OpenAIModule
from agentkernel.telegram import AgentTelegramRequestHandler

from agent import AGENTS
from telegram_ux import (
    campaign_approved_message,
    campaign_keyboard,
    caption_approved_message,
    caption_keyboard,
    caption_pack_message,
    content_approved_keyboard,
    content_approved_message,
    content_draft_message,
    content_keyboard,
    context_updated_message,
    event_created_message,
    event_keyboard,
    flyer_approved_keyboard,
    flyer_approved_message,
    flyer_keyboard,
    flyer_photo_caption,
    help_message,
    load_payload,
    parse_callback_data,
    publish_keyboard,
    publish_results_message,
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
    create_event_context,
    draft_flyer_content,
    edit_caption_pack,
    edit_flyer_content,
    generate_caption_pack,
    generate_flyer,
    get_campaign_status,
    get_event_context,
    ingest_direct_campaign_assets,
    publish_campaign,
    update_event_context,
)

OpenAIModule(AGENTS)

CHAT_STATE: dict[int, dict[str, str]] = {}
STATE_DIR_ENV = "CAMPAIGN_KERNEL_STATE_DIR"
DEFAULT_STATE_DIR = ".campaign_kernel_state"
FALSE_VALUES = {"0", "false", "no", "off"}

TELEGRAM_COMMANDS = [
    {"command": "start", "description": "Start CampaignKernel"},
    {"command": "new_event", "description": "Create a reusable event workspace"},
    {"command": "context", "description": "Add theme, colors, logos, or caption style"},
    {"command": "brief", "description": "Draft campaign content from a short brief"},
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
    return CHAT_STATE.setdefault(chat_id, {})


def _remember_chat(chat_id: int, event_id: str = "", campaign_id: str = "") -> None:
    active = _active(chat_id)
    if event_id:
        active["event_id"] = event_id
    if campaign_id:
        active["campaign_id"] = campaign_id


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
        "theme=",
        "colors=",
        "logo=",
        "asset=",
        "flyer=",
        "sample_caption=",
        "caption=",
        "style_analysis=",
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


def _state_dir() -> Path:
    return Path(os.environ.get(STATE_DIR_ENV, DEFAULT_STATE_DIR))


def _safe_file_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip(".-")
    return cleaned or "sample-flyer"


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
        if os.environ.get("CAMPAIGN_KERNEL_REGISTER_COMMANDS", "true").lower() not in FALSE_VALUES:
            threading.Thread(target=self._set_my_commands_sync, daemon=True).start()

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

    async def _save_uploaded_sample(self, event_id: str, message: dict[str, Any]) -> tuple[str, str]:
        file_id = ""
        file_name = "sample-flyer.jpg"
        if "photo" in message and message.get("photo"):
            file_id = message["photo"][-1].get("file_id", "")
        elif "document" in message:
            document = message.get("document", {})
            file_id = document.get("file_id", "")
            file_name = document.get("file_name", "sample-flyer")

        if not file_id:
            raise ValueError("No image or document file was found.")

        file_info = await self._get_file_info(file_id)
        if not file_info or not file_info.get("file_path"):
            raise ValueError("Telegram did not return a downloadable file path.")

        telegram_path = file_info["file_path"]
        if "photo" in message:
            file_name = Path(telegram_path).name or file_name

        content = await self._download_telegram_file(telegram_path)
        if content is None:
            raise ValueError("The sample file could not be downloaded.")

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
