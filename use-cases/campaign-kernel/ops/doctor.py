from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def check_url(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=12) as response:
            return {"ok": True, "status": response.status, "body": response.read().decode("utf-8")[:300]}
    except Exception as error:
        return {"ok": False, "error": str(error)}


def webhook_info(token: str) -> dict:
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=20) as response:
            return json.loads(response.read().decode("utf-8")).get("result", {})
    except Exception as error:
        return {"error": str(error)}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    load_env_file(root / ".env")

    state_dir = Path(os.environ.get("CAMPAIGN_KERNEL_STATE_DIR", root / ".campaign_kernel_state"))
    local_base = os.environ.get("CAMPAIGN_KERNEL_LOCAL_BASE_URL", "http://127.0.0.1:8000")
    public_base = os.environ.get("PUBLIC_BASE_URL", "")
    token = os.environ.get("AK_TELEGRAM__BOT_TOKEN", "")

    report = {
        "env": {
            "openai_key": bool(os.environ.get("OPENAI_API_KEY")),
            "openai_base_url": bool(os.environ.get("OPENAI_BASE_URL")),
            "openai_model": os.environ.get("OPENAI_MODEL", ""),
            "telegram_token": bool(token),
            "webhook_secret": bool(os.environ.get("AK_TELEGRAM__WEBHOOK_SECRET")),
            "mock_publish": os.environ.get("CAMPAIGN_KERNEL_MOCK_PUBLISH", "true"),
            "image_mode": os.environ.get("CAMPAIGN_KERNEL_IMAGE_MODE", "template"),
        },
        "state": {
            "path": str(state_dir),
            "exists": state_dir.exists(),
            "writable": os.access(state_dir if state_dir.exists() else state_dir.parent, os.W_OK),
        },
        "local_health": check_url(f"{local_base.rstrip('/')}/health"),
        "campaign_ready": check_url(f"{local_base.rstrip('/')}/campaign/ready"),
    }
    if public_base:
        report["public_health"] = check_url(f"{public_base.rstrip('/')}/health")
    if token:
        report["telegram_webhook"] = webhook_info(token)

    print(json.dumps(report, indent=2, sort_keys=True))
    failed = not report["env"]["telegram_token"] or not report["env"]["openai_key"] or not report["state"]["writable"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
