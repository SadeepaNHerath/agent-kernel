# CampaignKernel Agents

## Overview

CampaignKernel uses Agent Kernel as the orchestration layer for an event campaign workflow. The Telegram bot is the user-facing integration, while Agent Kernel agents call tools that store event context, generate structured flyer content, render flyers, create captions, manage approvals, and publish or export final packages.

## Agents

- `campaign_director`: Main routing agent. Decides whether the user is setting up an event, drafting content, editing, approving, packaging, or publishing.
- `event_context_agent`: Captures event-specific style context such as theme, colors, logo notes, uploaded sample flyers, sample captions, hashtag style, and caption structure.
- `flyer_content_agent`: Converts a short event brief into structured flyer content and asks for missing critical details before design work starts.
- `flyer_design_agent`: Generates or regenerates a flyer from approved content and stored design context.
- `caption_agent`: Produces platform-specific captions using the saved event caption pattern and tone.
- `approval_agent`: Enforces the approval workflow so content, flyer, caption, and final package are not published prematurely.
- `publisher_agent`: Publishes through configured adapters or creates mock previews/export packages when live credentials are unavailable.

## Tools

- `create_event_context`
- `update_event_context`
- `analyze_sample_flyer_context`
- `get_event_context`
- `draft_flyer_content`
- `edit_flyer_content`
- `approve_flyer_content`
- `generate_flyer`
- `approve_flyer`
- `generate_caption_pack`
- `edit_caption_pack`
- `approve_caption_pack`
- `ingest_direct_campaign_assets`
- `approve_campaign_package`
- `publish_campaign`
- `get_campaign_status`

## State And Memory

State is stored in `.campaign_kernel_state/state.json` by default, or in the directory set by `CAMPAIGN_KERNEL_STATE_DIR`. This keeps demo data outside Git while allowing repeatable local runs.

Each event stores:

- event name and ID
- theme notes
- tone
- colors
- logo notes
- asset references
- uploaded sample asset references
- sample flyer notes
- structured sample flyer analysis
- sample captions
- caption structure
- default hashtags
- campaign history

Each campaign stores:

- original brief
- flyer content draft and approval state
- flyer output path and approval state
- flyer mode, preview path, and AI fallback metadata
- caption pack and approval state
- final approval state
- mock or live publishing results

## Competition Demo Path

1. Create an event context with `/new_event`.
2. Add sample caption and style notes with `/context`.
3. Create a campaign brief with `/brief`.
4. Upload sample flyers with caption `sample for ck-event-id` so the bot stores layout, palette, hierarchy, CTA style, and caption cues.
5. Review and approve flyer content with inline buttons.
6. Generate the flyer; the Telegram bot sends the PNG directly into chat.
7. Approve or regenerate the flyer with buttons.
8. Generate and approve captions with buttons.
9. Approve the full package with buttons.
10. Mock publish to Instagram/Facebook/LinkedIn or export WhatsApp-ready content with buttons or `/publish` and `/export`.

## Safety Rules

- Do not publish before content, flyer, caption, and final package are approved.
- Block offensive, illegal, or illegible campaign content.
- Ask for missing date, venue, or CTA before flyer generation.
- Use mock publishing by default.
- Treat WhatsApp as export/share-ready content, not a public auto-posting platform.
