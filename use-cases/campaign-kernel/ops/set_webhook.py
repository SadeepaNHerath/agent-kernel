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
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    load_env_file(root / ".env")

    token = os.environ.get("AK_TELEGRAM__BOT_TOKEN", "")
    public_base = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PUBLIC_BASE_URL", "")).strip().rstrip("/")
    secret = os.environ.get("AK_TELEGRAM__WEBHOOK_SECRET", "")
    if not token:
        print("AK_TELEGRAM__BOT_TOKEN is not set.", file=sys.stderr)
        return 1
    if not public_base:
        print("Set PUBLIC_BASE_URL or pass it as the first argument.", file=sys.stderr)
        return 1

    payload = {"url": f"{public_base}/telegram/webhook", "drop_pending_updates": True}
    if secret:
        payload["secret_token"] = secret
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/setWebhook",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            print(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        print(error.read().decode("utf-8", errors="replace"), file=sys.stderr)
        return 1
    except urllib.error.URLError as error:
        print(f"Could not set webhook: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
