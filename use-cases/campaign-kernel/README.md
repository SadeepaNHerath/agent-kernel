# CampaignKernel

CampaignKernel is an Agent Kernel event campaign production agent for Telegram-driven campaign workflows. It helps event teams create consistent flyer content, generated flyers, captions, and publish/export packages using event-specific style memory.

## Problem Statement

Student clubs, NGOs, AIESEC teams, and small event organizers often run many campaigns with limited design and editing support. They have logos, sample flyers, caption styles, and brand rules, but each new event still requires manual coordination between organizers, designers, editors, and social media admins.

This creates delays, inconsistent captions, missing event details, and repeated back-and-forth before a campaign can be published.

## Solution Overview

CampaignKernel turns Telegram and a lightweight web workspace into a campaign production system for non-programmers.

Before an event, the user provides:

- logos and asset notes
- design theme and colors
- example flyers
- example captions
- caption structure and hashtag style

For each campaign, the user can choose either path:

- **Full creation path:** short brief -> flyer content -> button approval -> generated flyer image in chat -> edits/regeneration -> caption pack -> approval -> final package.
- **Designer/editor path:** upload or reference a ready flyer and/or ready caption -> validate -> package -> approve -> publish/export.

The normal Telegram experience uses short replies and inline buttons. Raw JSON is hidden from the happy path and kept for `/debug_status` when developers need it.

The web workspace adds a judge-friendly product surface:

- one-click campaign pack generation from a messy brief
- generated flyer preview through safe artifact URLs
- SDG classification and visible SDG flyer badge
- impact goals, campaign quality score, accessibility checks, and compliance review
- English, Sinhala, and Tamil caption variants
- audience/platform variants
- impact dashboard and exportable Markdown impact report
- documentation page at `/campaign/docs`

CampaignKernel uses Agent Kernel for:

- Telegram user-facing integration.
- Multi-agent campaign workflow.
- Tool calling for event context, campaign state, flyer rendering, captions, approvals, and publishing.
- Memory/state for event-specific style and campaign history.
- Strategy and memory agents for SDGs, impact, accessibility, quality scoring, organization memory, and partner memory.

Publishing defaults to mock previews so the competition demo is reliable. Live Facebook, Instagram, and LinkedIn publishing can be added when valid credentials are available. WhatsApp is treated as a share-ready export target.

## Setup Instructions

Prerequisites:

- Python 3.12.
- `uv` for dependency management.
- Telegram bot token from BotFather for Telegram demo.
- OpenAI or Azure OpenAI compatible API key for Agent Kernel agent execution.

Install dependencies:

```bash
chmod +x build.sh
./build.sh
```

Configure environment variables:

```bash
export OPENAI_API_KEY="your-azure-openai-key"
export OPENAI_BASE_URL="https://tantalum-resource.openai.azure.com/openai/v1"
export OPENAI_MODEL="gpt-5.5"
export AK_TELEGRAM__BOT_TOKEN="123456789:ABC..."
export AK_TELEGRAM__WEBHOOK_SECRET="campaign-kernel-secret"
export AK_MULTIMODAL__ENABLED=true
export CAMPAIGN_KERNEL_STATE_DIR=".campaign_kernel_state"
export CAMPAIGN_KERNEL_MOCK_PUBLISH=true
```

`OPENAI_MODEL` is passed to every CampaignKernel agent. For Azure OpenAI, use the model or deployment name exposed by your Azure OpenAI resource.

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

Optional flyer image mode:

```bash
# Default and recommended for the competition demo
export CAMPAIGN_KERNEL_IMAGE_MODE=template

# Optional. If image credentials/model are missing or the API fails,
# CampaignKernel falls back to the deterministic template renderer.
export CAMPAIGN_KERNEL_IMAGE_MODE=ai
export CAMPAIGN_KERNEL_IMAGE_MODEL="gpt-image-1"
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

Open the web workspace:

```text
http://127.0.0.1:8000/
```

Open the product documentation page:

```text
http://127.0.0.1:8000/campaign/docs
```

The server registers the main slash commands with Telegram on startup. You can also refresh the slash menu manually:

```bash
uv run python register_commands.py
```

Production-style local run:

```bash
chmod +x ops/run_local.sh ops/start_pinggy.sh
ops/run_local.sh
uv run python ops/doctor.py
```

Readiness endpoints:

```bash
curl http://127.0.0.1:8000/campaign/ready
curl http://127.0.0.1:8000/campaign/diagnostics
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

Try this Telegram product demo:

```text
/new_event IDEALIZE AI Workshop
/context theme=modern tech, confident student tone colors=blue, white caption=Hook, details, CTA, hashtags sample_caption=Ready to build with AI? Register now. #IDEALIZE #AIWorkshop
```

Upload one or more sample flyer images/PDFs with this caption:

```text
sample for ck-idealize-ai-workshop
```

CampaignKernel builds an event style profile across all samples, including layout, palette, background tone, accent structure, CTA style, caption hook pattern, and recurring hashtags.

Then send:

```text
/brief Free AI workshop for university students on July 25 at University of Moratuwa. Register via link in bio.
```

From there, use the buttons:

- `Approve Content`
- `One-Click Pack` to generate the flyer, captions, SDG intelligence, impact goals, quality score, and report path together
- `Generate Flyer`
- Review the flyer image sent in chat, then `Approve Flyer` or `Regenerate`
- For visual edits, send natural directions such as `/generate_flyer make it minimal with more whitespace` or `/generate_flyer dark bold centered poster with a larger CTA`
- `Generate Captions`
- For caption edits, send `/edit make it shorter and more professional` or `/edit make it more energetic, no hashtags`
- `Approve Caption`
- `Approve Campaign`
- `Publish IG/FB/LinkedIn` or `WhatsApp Export`

Power commands for the upgraded demo:

```text
/pack
/impact
/report
/dashboard
/schedule approval_due=2026-07-24 18:00 publish_at=2026-07-25 09:00 note=Final organizer check
/org_profile name=IDEALIZE colors=blue, white tone=confident student sdgs=4, 9 #IDEALIZE
/partner name=Green Society type=student partner wording=community partner logo=footer lockup
```

Designer/editor shortcut:

```text
/direct flyer=output/final.png caption=Join our free AI workshop...
Then approve the final package with buttons.
```

Run tests:

```bash
uv run pytest
```

For the full non-code checklist before the demo/submission, see `MANUAL_SETUP.md`.
For free/local production hardening and VM handoff, see `PRODUCTION_READINESS.md`.

## Competition Notes

- The code is inside the Agent Kernel repository under `use-cases/campaign-kernel`.
- The project uses Telegram as the supported user-facing integration.
- The web workspace shows the same agent workflow without needing Telegram during judging.
- The workflow demonstrates multiple agents, tools, memory/state, multimodal campaign context, SDG intelligence, and approval-driven publishing.
- Uploaded sample flyers are saved and analyzed into reusable style context: layout, palette, hierarchy, CTA style, and caption pattern.
- Generated flyers are delivered directly to Telegram with `sendPhoto`.
- Generated flyers include a visible SDG badge and are available through safe web artifact URLs.
- The default mock-publish path avoids live API credential risk during judging.
