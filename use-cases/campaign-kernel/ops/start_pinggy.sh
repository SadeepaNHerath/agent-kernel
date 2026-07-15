#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p .campaign_kernel_state

echo "Starting a free Pinggy tunnel to localhost:8000."
echo "Copy the HTTPS URL printed below, then run:"
echo "  uv run python ops/set_webhook.py https://your-free-url"
echo

exec ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -p 443 -R0:localhost:8000 a.pinggy.io
