import os

from agentkernel.openai import OpenAIToolBuilder
from agents import Agent

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

CAMPAIGN_TOOLS = [
    create_event_context,
    update_event_context,
    analyze_sample_flyer_context,
    get_event_context,
    draft_flyer_content,
    edit_flyer_content,
    approve_flyer_content,
    generate_flyer,
    approve_flyer,
    generate_caption_pack,
    edit_caption_pack,
    approve_caption_pack,
    enrich_campaign_intelligence,
    generate_one_click_campaign_pack,
    generate_campaign_impact_report,
    get_impact_dashboard,
    schedule_campaign,
    save_organization_profile,
    save_partner_memory,
    ingest_direct_campaign_assets,
    approve_campaign_package,
    publish_campaign,
    get_campaign_status,
]

CAMPAIGN_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.5")

event_context_agent = Agent(
    name="event_context_agent",
    model=CAMPAIGN_MODEL,
    handoff_description="Stores and retrieves event-specific brand, design, logo, and caption style context.",
    instructions=(
        "Capture event context before campaign generation. Save theme notes, logo notes, sample flyer notes, "
        "sample captions, caption structure, colors, and hashtags with the provided tools. Summarize the stored "
        "style clearly and ask for confirmation when context is incomplete."
    ),
    tools=OpenAIToolBuilder.bind(
        [create_event_context, update_event_context, analyze_sample_flyer_context, get_event_context]
    ),
)

flyer_content_agent = Agent(
    name="flyer_content_agent",
    model=CAMPAIGN_MODEL,
    handoff_description="Drafts and edits structured flyer content from a short campaign brief.",
    instructions=(
        "Turn short briefs into structured flyer content. Always check date, venue, and CTA before asking the user "
        "to approve the content. Apply user edits through the content tools instead of inventing untracked drafts."
    ),
    tools=OpenAIToolBuilder.bind([draft_flyer_content, edit_flyer_content, approve_flyer_content, get_campaign_status]),
)

flyer_design_agent = Agent(
    name="flyer_design_agent",
    model=CAMPAIGN_MODEL,
    handoff_description="Generates and revises event flyers after the content has been approved.",
    instructions=(
        "Generate flyers only after content approval. Use the saved event style and any design instruction. "
        "If the user asks for visual edits, regenerate the flyer and keep the approval pending until confirmed."
    ),
    tools=OpenAIToolBuilder.bind([generate_flyer, approve_flyer, get_event_context, get_campaign_status]),
)

caption_agent = Agent(
    name="caption_agent",
    model=CAMPAIGN_MODEL,
    handoff_description="Creates platform-specific captions in the saved event style.",
    instructions=(
        "Create Instagram, Facebook, LinkedIn, and WhatsApp-export captions after the flyer is approved. Match the "
        "event's saved caption pattern and keep alt text accessible. Do not mark captions approved until the user says so."
    ),
    tools=OpenAIToolBuilder.bind([generate_caption_pack, edit_caption_pack, approve_caption_pack, get_campaign_status]),
)

approval_agent = Agent(
    name="approval_agent",
    model=CAMPAIGN_MODEL,
    handoff_description="Controls content, flyer, caption, and final campaign approval gates.",
    instructions=(
        "Protect the campaign workflow from premature publishing. Verify content, flyer, caption, and final package "
        "approvals before publishing. If an approval is missing, explain the next required command."
    ),
    tools=OpenAIToolBuilder.bind(
        [approve_flyer_content, approve_flyer, approve_caption_pack, approve_campaign_package, get_campaign_status]
    ),
)

publisher_agent = Agent(
    name="publisher_agent",
    model=CAMPAIGN_MODEL,
    handoff_description="Publishes approved campaign packages or creates mock/export previews.",
    instructions=(
        "Publish only after final campaign approval. Use mock publishing by default. Treat WhatsApp as an export "
        "target, not a public auto-posting target. Explain missing credentials when live publishing is requested."
    ),
    tools=OpenAIToolBuilder.bind([publish_campaign, get_campaign_status]),
)

campaign_strategy_agent = Agent(
    name="campaign_strategy_agent",
    model=CAMPAIGN_MODEL,
    handoff_description="Adds SDG alignment, impact goals, campaign strategy, CTAs, variants, and quality scoring.",
    instructions=(
        "Use campaign intelligence tools to classify SDGs, generate measurable impact goals, optimize CTAs, "
        "create audience/platform variants, check accessibility, and produce a campaign quality score. Keep advice "
        "specific to the saved event context and approved campaign materials."
    ),
    tools=OpenAIToolBuilder.bind(
        [
            enrich_campaign_intelligence,
            generate_one_click_campaign_pack,
            generate_campaign_impact_report,
            get_impact_dashboard,
            schedule_campaign,
            get_campaign_status,
        ]
    ),
)

campaign_memory_agent = Agent(
    name="campaign_memory_agent",
    model=CAMPAIGN_MODEL,
    handoff_description="Maintains organization and partner memory used by campaign generation and reporting.",
    instructions=(
        "Save reusable organization and partner context such as brand colors, tone, recurring hashtags, preferred "
        "SDGs, sponsor wording, and logo usage. Prefer updating memory over repeating the same context each campaign."
    ),
    tools=OpenAIToolBuilder.bind(
        [save_organization_profile, save_partner_memory, get_event_context, get_impact_dashboard]
    ),
)

campaign_director = Agent(
    name="campaign_director",
    model=CAMPAIGN_MODEL,
    handoff_description="Main CampaignKernel coordinator for Telegram and CLI users.",
    instructions=(
        "You are CampaignKernel, an event campaign production agent. Keep users inside this workflow: event context, "
        "flyer content, content approval, flyer generation, flyer approval, caption generation, caption approval, "
        "final approval, then publish/export. Use tools for all state changes. If a user already has a designer or "
        "editor, use ingest_direct_campaign_assets to package their supplied flyer or caption. Keep responses concise "
        "for Telegram and show the next command. For demos, use generate_one_click_campaign_pack to turn a rough brief "
        "into a flyer, caption pack, SDG intelligence, impact goals, and quality score quickly."
    ),
    handoffs=[
        event_context_agent,
        flyer_content_agent,
        flyer_design_agent,
        caption_agent,
        approval_agent,
        publisher_agent,
        campaign_strategy_agent,
        campaign_memory_agent,
    ],
    tools=OpenAIToolBuilder.bind(CAMPAIGN_TOOLS),
)

AGENTS = [
    campaign_director,
    event_context_agent,
    flyer_content_agent,
    flyer_design_agent,
    caption_agent,
    approval_agent,
    publisher_agent,
    campaign_strategy_agent,
    campaign_memory_agent,
]
