#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

export CAMPAIGN_KERNEL_STATE_DIR="${CAMPAIGN_KERNEL_STATE_DIR:-.campaign_kernel_state}"
mkdir -p "$CAMPAIGN_KERNEL_STATE_DIR"

exec uv run python server.py
