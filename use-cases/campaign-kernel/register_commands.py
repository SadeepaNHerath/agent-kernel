from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

COMMANDS = [
    {"command": "start", "description": "Start CampaignKernel"},
    {"command": "new_event", "description": "Create a reusable event workspace"},
    {"command": "context", "description": "Add theme, colors, logos, or caption style"},
    {"command": "brief", "description": "Draft campaign content from a short brief"},
    {"command": "status", "description": "Show current event or campaign status"},
    {"command": "help", "description": "Show the simple workflow"},
]


def main() -> int:
    token = os.environ.get("AK_TELEGRAM__BOT_TOKEN", "")
    if not token:
        print("AK_TELEGRAM__BOT_TOKEN is not set.", file=sys.stderr)
        return 1

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/setMyCommands",
        data=json.dumps({"commands": COMMANDS}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            print(response.read().decode("utf-8"))
    except urllib.error.URLError as error:
        print(f"Could not register commands: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
