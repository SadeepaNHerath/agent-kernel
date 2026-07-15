from __future__ import annotations

import asyncio

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
