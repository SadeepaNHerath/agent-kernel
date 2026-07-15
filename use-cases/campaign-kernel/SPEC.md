# CampaignKernel Specification

## Agent Description

CampaignKernel is an Agent Kernel event campaign production agent. It helps student clubs, NGOs, AIESEC teams, and small organizers turn a short event brief plus event-specific brand context into approved flyer content, a generated flyer, platform-specific captions, and a publish/export package.

The primary interface is Telegram. A separate Telegram event chat or channel can be used for each event. Before a campaign, the user provides logos, assets, theme notes, uploaded sample flyers, and sample captions. CampaignKernel stores this event context and reuses the same caption pattern, visual style, and tone across future campaign material.

## Functional Requirements

- Build an Agent Kernel use case under `use-cases/campaign-kernel`.
- Use Telegram as the user-facing integration.
- Support event setup with theme notes, colors, logo notes, sample flyer notes, uploaded sample flyer analysis, aggregate multi-sample style profile, sample captions, caption structure, and default hashtags.
- Register Telegram slash commands for the main workflow.
- Use short user-facing messages and inline buttons instead of raw JSON in normal Telegram replies.
- Send generated flyer images directly into Telegram chat.
- Keep raw state available through developer-only debug commands.
- Support a full creation workflow:
  - event brief
  - flyer content draft
  - content edits
  - content approval
  - flyer generation with template mode by default, prompt-responsive layout variants, and optional AI image mode with fallback
  - flyer edits/regeneration
  - flyer approval
  - caption pack generation
  - caption edits that rewrite the platform captions according to the prompt
  - caption approval
  - final campaign approval
  - publish or export
- Support a designer/editor workflow where the user directly provides a final flyer and/or final caption, then asks CampaignKernel to validate, package, and publish/export it.
- Use Agent Kernel tools for event context, campaign state, content generation, flyer rendering, captions, approval gates, and publisher adapters.
- Use session or persistent state to remember event context and campaign history.
- Remember active event and campaign IDs per Telegram chat where possible so users do not need to retype IDs.
- Default publishing must be mock-safe for competition demos. Live publishing can be enabled only with explicit credentials.
- Treat WhatsApp as a share-ready export target, not a public auto-posting target.

## Agent Kernel Requirements

- Register multiple OpenAI Agents SDK agents through `OpenAIModule`.
- Bind deterministic campaign tools through `OpenAIToolBuilder`.
- Provide a local CLI entry point through Agent Kernel CLI.
- Provide a Telegram webhook server using Agent Kernel's Telegram request handler.
- Keep generated state and output artifacts out of Git.

## Required Documentation

README.md must include:

1. Problem statement
2. Solution overview
3. Setup instructions
4. How to run the solution

AGENTS.md must explain the agents, tools, memory/state, workflow, and demo path.
