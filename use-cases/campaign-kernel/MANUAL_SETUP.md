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
- Type `/` in Telegram and confirm `start`, `new_event`, `context`, `brief`, `status`, and `help` are visible.
- Create one Telegram chat/channel per event campaign if the team wants separate event workspaces.

The server registers slash commands on startup. If the commands do not appear, run:

```bash
uv run python register_commands.py
```

Run the production-style doctor before a demo:

```bash
uv run python ops/doctor.py
```

## Required Local Environment

```bash
export OPENAI_API_KEY="your-azure-openai-key"
export OPENAI_BASE_URL="https://tantalum-resource.openai.azure.com/openai/v1"
export OPENAI_MODEL="gpt-5.5"
export AK_TELEGRAM__BOT_TOKEN="..."
export AK_TELEGRAM__WEBHOOK_SECRET="campaign-kernel-secret"
export AK_MULTIMODAL__ENABLED=true
export CAMPAIGN_KERNEL_MOCK_PUBLISH=true
export CAMPAIGN_KERNEL_MAX_UPLOAD_MB=12
```

For Azure OpenAI, `OPENAI_MODEL` should match the model/deployment name configured on the Azure OpenAI resource.

Flyer rendering defaults to the reliable template renderer. Optional AI image mode can be enabled, but it falls back to template mode when image credentials or model support are missing:

```bash
export CAMPAIGN_KERNEL_IMAGE_MODE=template
# or
export CAMPAIGN_KERNEL_IMAGE_MODE=ai
export CAMPAIGN_KERNEL_IMAGE_MODEL="gpt-image-1"
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
/context theme=modern tech, confident student tone colors=blue, white caption=Hook, details, CTA, hashtags sample_caption=Ready to build with AI? Register now. #IDEALIZE #AIWorkshop
```

Upload a sample flyer image or PDF with this caption:

```text
sample for ck-idealize-ai-workshop
```

Then send the brief:

```text
/brief Free AI workshop for university students on July 25 at University of Moratuwa. Register via link in bio.
```

Click buttons in this order:

- `Approve Content`
- `Generate Flyer`
- Check the flyer image sent in Telegram
- `Approve Flyer`
- `Generate Captions`
- `Approve Caption`
- `Approve Campaign`
- `Publish IG/FB/LinkedIn`

Designer/editor path:

```text
/direct flyer=output/final.png caption=Join our workshop. Register now. #IDEALIZE
```

Then approve the campaign and choose `WhatsApp Export` with buttons.

## Debug Commands

Normal users should not need these:

- `/debug_status` returns raw campaign JSON for developers.
- `/raw_event` returns raw event context JSON.
- Power users can still pass explicit IDs to every command, for example `/brief ck-event-id ...`.
