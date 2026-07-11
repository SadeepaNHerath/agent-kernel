# CampaignKernel

CampaignKernel is an Agent Kernel event campaign production agent for Telegram-driven campaign workflows. It helps event teams create consistent flyer content, generated flyers, captions, and publish/export packages using event-specific style memory.

## Problem Statement

Student clubs, NGOs, AIESEC teams, and small event organizers often run many campaigns with limited design and editing support. They have logos, sample flyers, caption styles, and brand rules, but each new event still requires manual coordination between organizers, designers, editors, and social media admins.

This creates delays, inconsistent captions, missing event details, and repeated back-and-forth before a campaign can be published.

## Solution Overview

CampaignKernel turns Telegram into a campaign production workspace.

Before an event, the user provides:

- logos and asset notes
- design theme and colors
- example flyers
- example captions
- caption structure and hashtag style

For each campaign, the user can choose either path:

- **Full creation path:** short brief -> flyer content -> approval -> generated flyer -> edits -> caption pack -> approval -> final package.
- **Designer/editor path:** upload or reference a ready flyer and/or ready caption -> validate -> package -> approve -> publish/export.

CampaignKernel uses Agent Kernel for:

- Telegram user-facing integration.
- Multi-agent campaign workflow.
- Tool calling for event context, campaign state, flyer rendering, captions, approvals, and publishing.
- Memory/state for event-specific style and campaign history.

Publishing defaults to mock previews so the competition demo is reliable. Live Facebook, Instagram, and LinkedIn publishing can be added when valid credentials are available. WhatsApp is treated as a share-ready export target.

## Setup Instructions

Prerequisites:

- Python 3.12.
- `uv` for dependency management.
- Telegram bot token from BotFather for Telegram demo.
- OpenAI API key for Agent Kernel agent execution.

Install dependencies:

```bash
chmod +x build.sh
./build.sh
```

Configure environment variables:

```bash
export OPENAI_API_KEY="sk-..."
export AK_TELEGRAM__BOT_TOKEN="123456789:ABC..."
export AK_TELEGRAM__WEBHOOK_SECRET="campaign-kernel-secret"
export AK_MULTIMODAL__ENABLED=true
export CAMPAIGN_KERNEL_STATE_DIR=".campaign_kernel_state"
export CAMPAIGN_KERNEL_MOCK_PUBLISH=true
```

Optional live publishing variables:

```bash
export CAMPAIGN_KERNEL_MOCK_PUBLISH=false
export CAMPAIGN_KERNEL_LIVE_PUBLISH=true

# Facebook Page adapter
export FACEBOOK_PAGE_ID="..."
export FACEBOOK_PAGE_ACCESS_TOKEN="..."

# Instagram adapter
export INSTAGRAM_USER_ID="..."
export INSTAGRAM_ACCESS_TOKEN="..."

# LinkedIn adapter
export LINKEDIN_AUTHOR_URN="urn:li:organization:..."
export LINKEDIN_ACCESS_TOKEN="..."
```

## How To Run The Solution

Run the local Agent Kernel CLI:

```bash
uv run python demo.py
```

Run the Telegram webhook server:

```bash
uv run python server.py
```

Expose the local server:

```bash
ngrok http 8000
```

Set the Telegram webhook:

```bash
curl -X POST "https://api.telegram.org/bot$AK_TELEGRAM__BOT_TOKEN/setWebhook" \
  -H "Content-Type: application/json" \
  -d "{
    \"url\": \"https://your-ngrok-url.ngrok-free.app/telegram/webhook\",
    \"secret_token\": \"$AK_TELEGRAM__WEBHOOK_SECRET\"
  }"
```

Try this Telegram demo:

```text
/new_event IDEALIZE AI Workshop
/context CK-idealize-ai-workshop theme=modern tech, blue and white, confident student tone; hashtags=#IDEALIZE #AIESEC #AIWorkshop; caption=Hook, details, CTA, hashtags
/brief CK-idealize-ai-workshop Free AI workshop for university students on July 25 at University of Moratuwa. Register via link in bio.
/approve_content CK-idealize-ai-workshop CK-0001
/generate_flyer CK-idealize-ai-workshop CK-0001
/approve_flyer CK-idealize-ai-workshop CK-0001
/caption CK-idealize-ai-workshop CK-0001
/approve_caption CK-idealize-ai-workshop CK-0001
/approve_campaign CK-idealize-ai-workshop CK-0001
/publish CK-idealize-ai-workshop CK-0001 instagram facebook linkedin
```

Designer/editor shortcut:

```text
/direct CK-idealize-ai-workshop flyer=output/final.png caption=Join our free AI workshop...
/approve_campaign CK-idealize-ai-workshop CK-0002
/export CK-idealize-ai-workshop CK-0002 whatsapp
```

Run tests:

```bash
uv run pytest
```

For the full non-code checklist before the demo/submission, see `MANUAL_SETUP.md`.

## Competition Notes

- The code is inside the Agent Kernel repository under `use-cases/campaign-kernel`.
- The project uses Telegram as the supported user-facing integration.
- The workflow demonstrates multiple agents, tools, memory/state, multimodal campaign context, and approval-driven publishing.
- The default mock-publish path avoids live API credential risk during judging.
