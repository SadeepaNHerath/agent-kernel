# CampaignKernel Production Readiness

CampaignKernel is now prepared for a free/local production-style test and a later VM deployment. The current free setup still depends on a public tunnel, so tunnel expiry remains the main reliability limit until a VM/domain is used.

## What Is Hardened

- Persistent Telegram chat state in `.campaign_kernel_state/chat_state.json`.
- Telegram update de-duplication in `.campaign_kernel_state/processed_updates.json`.
- `/campaign/ready` and `/campaign/diagnostics` endpoints.
- Upload size/type checks for sample flyers.
- Clear runtime warnings for missing key environment settings.
- Free/local ops scripts for running, webhook setup, tunnel setup, and health checks.
- Mock publishing remains the safe default.

## Local Free Run

Terminal 1:

```bash
cd /Users/sadeepaherath/Downloads/IDEALIZE/agent-kernel/use-cases/campaign-kernel
chmod +x ops/run_local.sh ops/start_pinggy.sh
ops/run_local.sh
```

Terminal 2:

```bash
cd /Users/sadeepaherath/Downloads/IDEALIZE/agent-kernel/use-cases/campaign-kernel
ops/start_pinggy.sh
```

Copy the HTTPS tunnel URL and set the webhook:

```bash
uv run python ops/set_webhook.py https://your-public-url
```

Check readiness:

```bash
uv run python ops/doctor.py
curl http://127.0.0.1:8000/campaign/ready
```

## Required Environment

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://your-resource.openai.azure.com/openai/v1
OPENAI_MODEL=your-deployment-name
AK_TELEGRAM__BOT_TOKEN=...
AK_TELEGRAM__WEBHOOK_SECRET=...
AK_MULTIMODAL__ENABLED=true
CAMPAIGN_KERNEL_MOCK_PUBLISH=true
CAMPAIGN_KERNEL_STATE_DIR=.campaign_kernel_state
CAMPAIGN_KERNEL_MAX_UPLOAD_MB=12
```

## VM Handoff

On a VM:

1. Clone the repository into `/opt/agent-kernel`.
2. Install Python 3.12 and `uv`.
3. Create a dedicated `campaignkernel` user.
4. Put production `.env` in `/opt/agent-kernel/use-cases/campaign-kernel/.env`.
5. Copy `ops/campaign-kernel.service.example` to `/etc/systemd/system/campaign-kernel.service`.
6. Update paths/user if needed.
7. Run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable campaign-kernel
sudo systemctl start campaign-kernel
sudo systemctl status campaign-kernel
```

Set Telegram webhook to the VM HTTPS domain:

```bash
uv run python ops/set_webhook.py https://your-domain.example
```

## Still Needed For True SaaS Production

- Stable HTTPS domain instead of free tunnel.
- Backups for `.campaign_kernel_state`.
- Proper database if multiple instances are needed.
- Private file storage for generated flyers and uploads.
- Auth/roles if multiple teams use the same bot.
- Monitoring/alerts for webhook errors and failed publish actions.
- Real Meta/LinkedIn credentials if live publishing is enabled.
