from __future__ import annotations

import asyncio

import pytest

import server
from server import CampaignTelegramHandler
from telegram_ux import (
    callback_data,
    content_draft_message,
    flyer_photo_caption,
    parse_callback_data,
    publish_keyboard,
)


def test_content_formatter_is_short_and_human():
    message = content_draft_message(
        {
            "ok": True,
            "campaign": {
                "campaign_id": "CK-0001",
                "missing_fields": [],
                "content": {
                    "title": "AI Workshop",
                    "date": "July 25",
                    "venue": "University of Moratuwa",
                    "cta": "Register now",
                },
            },
        }
    )

    assert "Content draft ready." in message
    assert "{" not in message
    assert "Campaign ID: CK-0001" in message


def test_callback_data_maps_to_action():
    data = callback_data("approve_content", "ck-demo-event", "CK-0001")
    callback = parse_callback_data(data)

    assert callback.action == "approve_content"
    assert callback.event_id == "ck-demo-event"
    assert callback.campaign_id == "CK-0001"


def test_publish_keyboard_uses_short_callback_data():
    markup = publish_keyboard("ck-idealize-ai-workshop", "CK-0001")
    callback = markup["inline_keyboard"][0][0]["callback_data"]

    assert len(callback) <= 64
    assert parse_callback_data(callback).value == "social"


def test_flyer_result_sends_photo_action():
    handler = object.__new__(CampaignTelegramHandler)
    sent = {}

    async def fake_send_photo(chat_id, photo_path, caption, reply_markup=None):
        sent["chat_id"] = chat_id
        sent["photo_path"] = photo_path
        sent["caption"] = caption
        sent["reply_markup"] = reply_markup

    handler._send_photo = fake_send_photo

    asyncio.run(
        handler._send_flyer_result(
            123,
            {
                "ok": True,
                "event_id": "ck-demo-event",
                "campaign_id": "CK-0001",
                "flyer": {"path": "/tmp/flyer.png", "version": 2, "mode": "template"},
            },
        )
    )

    assert sent["chat_id"] == 123
    assert sent["photo_path"] == "/tmp/flyer.png"
    assert "Flyer draft ready." in sent["caption"]
    assert sent["reply_markup"]["inline_keyboard"][0][0]["text"] == "Approve Flyer"


def test_flyer_caption_mentions_template_fallback():
    caption = flyer_photo_caption(
        {
            "ok": True,
            "campaign_id": "CK-0001",
            "flyer": {"version": 1, "mode": "template", "fallback_reason": "missing image model"},
        }
    )

    assert "AI fallback" in caption


def test_channel_post_routes_to_message_handler():
    handler = object.__new__(CampaignTelegramHandler)
    seen = {}

    class FakeLog:
        def debug(self, *args, **kwargs):
            pass

        def error(self, *args, **kwargs):
            pass

    async def fake_handle_message(message):
        seen["message"] = message

    handler._log = FakeLog()
    handler._handle_message = fake_handle_message

    asyncio.run(
        handler._process_webhook_body({"channel_post": {"message_id": 7, "chat": {"id": -100123}, "text": "/start"}})
    )

    assert seen["message"]["text"] == "/start"


def test_upload_with_active_event_is_saved_without_multimodal(monkeypatch):
    server.CHAT_STATE.clear()
    server.CHAT_STATE[123] = {"event_id": "ck-demo-event"}
    handler = object.__new__(CampaignTelegramHandler)
    sent = {}

    async def fake_send_chat_action(chat_id, action):
        sent["action"] = action

    async def fake_save_uploaded_sample(event_id, message):
        sent["event_id"] = event_id
        return "sample.jpg", "/tmp/sample.jpg"

    async def fake_send_message(chat_id, text, parse_mode=None, reply_markup=None):
        sent["chat_id"] = chat_id
        sent["text"] = text
        sent["reply_markup"] = reply_markup

    def fake_analyze_sample_flyer_context(event_id, file_name="", caption="", file_path="", analysis_notes=""):
        sent["analyzed"] = {
            "event_id": event_id,
            "file_name": file_name,
            "caption": caption,
            "file_path": file_path,
        }
        return (
            '{"ok": true, "event_id": "ck-demo-event", '
            '"style_analysis": {"layout": "Tall portrait poster layout", '
            '"typography_feel": "bold title hierarchy", "color_palette": ["#1D4ED8"]}}'
        )

    async def fail_process_agent_message(*args, **kwargs):
        raise AssertionError("Upload should not fall through to generic multimodal processing")

    monkeypatch.setattr(server, "analyze_sample_flyer_context", fake_analyze_sample_flyer_context)
    handler._send_chat_action = fake_send_chat_action
    handler._save_uploaded_sample = fake_save_uploaded_sample
    handler._send_message = fake_send_message
    handler._process_agent_message = fail_process_agent_message

    asyncio.run(
        handler._handle_message(
            {
                "message_id": 10,
                "chat": {"id": 123},
                "caption": "use this as the flyer preset",
                "photo": [{"file_id": "small"}, {"file_id": "large"}],
            }
        )
    )

    assert sent["event_id"] == "ck-demo-event"
    assert sent["analyzed"]["caption"] == "use this as the flyer preset"
    assert "Sample flyer saved." in sent["text"]


def test_upload_without_active_event_gets_clear_instruction():
    server.CHAT_STATE.clear()
    handler = object.__new__(CampaignTelegramHandler)
    sent = {}

    async def fake_send_message(chat_id, text, parse_mode=None, reply_markup=None):
        sent["chat_id"] = chat_id
        sent["text"] = text

    async def fail_process_agent_message(*args, **kwargs):
        raise AssertionError("Upload should not fall through to generic multimodal processing")

    handler._send_message = fake_send_message
    handler._process_agent_message = fail_process_agent_message

    asyncio.run(
        handler._handle_message(
            {
                "message_id": 11,
                "chat": {"id": 456},
                "caption": "sample flyer",
                "photo": [{"file_id": "large"}],
            }
        )
    )

    assert sent["chat_id"] == 456
    assert "Create one with /new_event" in sent["text"]


def test_chat_state_persists_to_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("CAMPAIGN_KERNEL_STATE_DIR", str(tmp_path / "state"))
    server.CHAT_STATE.clear()

    server._remember_chat(123, "ck-demo-event", "CK-0001")
    server.CHAT_STATE.clear()
    server.CHAT_STATE.update(server._load_chat_state())

    active = server._active(123)
    assert active["event_id"] == "ck-demo-event"
    assert active["campaign_id"] == "CK-0001"
    assert active["updated_at"]


def test_duplicate_update_id_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("CAMPAIGN_KERNEL_STATE_DIR", str(tmp_path / "state"))
    server.PROCESSED_UPDATE_IDS.clear()
    handler = object.__new__(CampaignTelegramHandler)
    calls = {"count": 0}

    class FakeLog:
        def debug(self, *args, **kwargs):
            pass

        def info(self, *args, **kwargs):
            pass

        def error(self, *args, **kwargs):
            pass

    async def fake_handle_message(message):
        calls["count"] += 1

    handler._log = FakeLog()
    handler._handle_message = fake_handle_message

    body = {"update_id": 777, "message": {"message_id": 1, "chat": {"id": 123}, "text": "/start"}}
    asyncio.run(handler._process_webhook_body(body))
    asyncio.run(handler._process_webhook_body(body))

    assert calls["count"] == 1


def test_rejects_unsupported_upload_type():
    handler = object.__new__(CampaignTelegramHandler)

    async def fake_get_file_info(file_id):
        return {"file_path": "documents/sample.txt", "file_size": 100}

    async def fail_download(file_path):
        raise AssertionError("Unsupported uploads should fail before download")

    handler._get_file_info = fake_get_file_info
    handler._download_telegram_file = fail_download

    with pytest.raises(ValueError, match="PNG, JPG, WEBP, or PDF"):
        asyncio.run(
            handler._save_uploaded_sample(
                "ck-demo-event",
                {
                    "message_id": 1,
                    "document": {
                        "file_id": "file-1",
                        "file_name": "sample.txt",
                        "mime_type": "text/plain",
                    },
                },
            )
        )


def test_rejects_oversized_upload(monkeypatch):
    monkeypatch.setenv("CAMPAIGN_KERNEL_MAX_UPLOAD_MB", "1")
    handler = object.__new__(CampaignTelegramHandler)

    async def fake_get_file_info(file_id):
        return {"file_path": "photos/sample.jpg", "file_size": 2 * 1024 * 1024}

    async def fail_download(file_path):
        raise AssertionError("Oversized uploads should fail before download")

    handler._get_file_info = fake_get_file_info
    handler._download_telegram_file = fail_download

    with pytest.raises(ValueError, match="too large"):
        asyncio.run(
            handler._save_uploaded_sample(
                "ck-demo-event",
                {
                    "message_id": 1,
                    "photo": [{"file_id": "small"}, {"file_id": "large"}],
                },
            )
        )
