# CampaignKernel Manual Setup Checklist

Use this checklist before recording the demo or submitting the mini-competition entry.

## Mini-Competition Requirements

- Every team member stars `https://github.com/yaalalabs/agent-kernel`.
- One team member forks the official Agent Kernel repository.
- The submitting member uses the forked repository link.
- Collect GitHub IDs for every team member.
- Join the Agent Kernel Discord and watch announcements.
- Read `CODE_OF_CONDUCT.md` and `DEVELOPER_GUIDE.md`.
- Keep all work in `use-cases/campaign-kernel`.
- Submit README.md, SPEC.md, AGENTS.md, and an optional max 5-minute demo video.

## Telegram Demo Setup

- Create a Telegram bot with BotFather.
- Copy the bot token into `AK_TELEGRAM__BOT_TOKEN`.
- Choose a random webhook secret and set `AK_TELEGRAM__WEBHOOK_SECRET`.
- Run `uv run python server.py`.
- Expose local port 8000 with ngrok, pinggy, or another HTTPS tunnel.
- Set Telegram webhook to `https://<public-url>/telegram/webhook`.
- Create one Telegram chat/channel per event campaign.

## Required Local Environment

```bash
export OPENAI_API_KEY="sk-..."
export AK_TELEGRAM__BOT_TOKEN="..."
export AK_TELEGRAM__WEBHOOK_SECRET="campaign-kernel-secret"
export AK_MULTIMODAL__ENABLED=true
export CAMPAIGN_KERNEL_MOCK_PUBLISH=true
```

## Optional Live Publishing

Use mock publishing for the competition unless live credentials are already working.

Facebook Page:

- Create or use a Facebook Page you manage.
- Create a Meta developer app.
- Generate a Page access token with publish permissions.
- Set `FACEBOOK_PAGE_ID`, `FACEBOOK_PAGE_ACCESS_TOKEN`, and `CAMPAIGN_KERNEL_PUBLIC_MEDIA_URL`.

Instagram:

- Use an Instagram Professional account.
- Configure Meta app permissions for content publishing.
- Set `INSTAGRAM_USER_ID`, `INSTAGRAM_ACCESS_TOKEN`, and `CAMPAIGN_KERNEL_PUBLIC_MEDIA_URL`.

LinkedIn:

- Create a LinkedIn developer app.
- Complete OAuth for the target member or organization.
- Set `LINKEDIN_AUTHOR_URN` and `LINKEDIN_ACCESS_TOKEN`.

WhatsApp:

- CampaignKernel exports WhatsApp-ready image/caption packages.
- Do not present WhatsApp as automatic public posting.

## Demo Script

```text
/new_event IDEALIZE AI Workshop
/context CK-idealize-ai-workshop theme=modern tech, confident student tone colors=blue, white caption=Hook, details, CTA, hashtags sample_caption=Ready to build with AI? Register now. #IDEALIZE #AIWorkshop
/brief CK-idealize-ai-workshop Free AI workshop for university students on July 25 at University of Moratuwa. Register via link in bio.
/approve_content CK-idealize-ai-workshop CK-0001
/generate_flyer CK-idealize-ai-workshop CK-0001 premium but student-friendly
/approve_flyer CK-idealize-ai-workshop CK-0001
/caption CK-idealize-ai-workshop CK-0001
/approve_caption CK-idealize-ai-workshop CK-0001
/approve_campaign CK-idealize-ai-workshop CK-0001
/publish CK-idealize-ai-workshop CK-0001 instagram facebook linkedin
```

Designer/editor path:

```text
/direct CK-idealize-ai-workshop flyer=output/final.png caption=Join our workshop. Register now. #IDEALIZE
/approve_campaign CK-idealize-ai-workshop CK-0002
/export CK-idealize-ai-workshop CK-0002 whatsapp
```
