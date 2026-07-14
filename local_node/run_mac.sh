#!/bin/bash
set -euo pipefail

SUPPORT_DIR="$HOME/Library/Application Support/JobSourceNode"
ENV_FILE="${JOB_SOURCE_NODE_ENV:-$SUPPORT_DIR/node.env}"
SOURCES="${1:-boss,liepin}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "未找到本地节点配置：$ENV_FILE" >&2
  echo "请先运行setup_mac.sh完成首次授权。" >&2
  exit 2
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

PYTHON="$JOB_SOURCE_NODE_VENV/bin/python"
CONFIG="$SUPPORT_DIR/config.mac.json"
LOG_DIR="$SUPPORT_DIR/logs"
LOCK_DIR="$SUPPORT_DIR/run.lock"
mkdir -p "$LOG_DIR" "$JOB_SOURCE_DROP_DIR"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "已有本地采集任务运行中，本次退出。" >&2
  exit 3
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

export PATH="$JOB_SOURCE_NODE_VENV/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export JOB_SOURCE_DROP_DIR LIEPIN_PROFILE_DIR
cd "$JOB_SOURCE_NODE_ROOT/local_node"

"$PYTHON" ../validation/source_registry_check.py
"$PYTHON" ../validation/source_control_plane.py health --context local \
  --output "$LOG_DIR/local-health-latest.json"
"$PYTHON" ../validation/source_control_plane.py collect-local \
  --config "$CONFIG" \
  --sources "$SOURCES"
