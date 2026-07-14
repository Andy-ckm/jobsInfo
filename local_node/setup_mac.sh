#!/bin/bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "此脚本仅适用于macOS。" >&2
  exit 2
fi

SOURCE_LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_ROOT="$(cd "$SOURCE_LOCAL_DIR/.." && pwd)"
SUPPORT_DIR="$HOME/Library/Application Support/JobSourceNode"
APP_DIR="$SUPPORT_DIR/app"
VENV_DIR="$SUPPORT_DIR/venv"
PROFILE_DIR="$SUPPORT_DIR/liepin-profile"
ENV_FILE="$SUPPORT_DIR/node.env"
LOG_DIR="$SUPPORT_DIR/logs"

if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到python3。请先安装Python 3.10或更高版本。推荐：brew install python" >&2
  exit 3
fi

mkdir -p "$SUPPORT_DIR" "$LOG_DIR" "$PROFILE_DIR"
if [[ "$SOURCE_ROOT" != "$APP_DIR" ]]; then
  rm -rf "$APP_DIR"
  mkdir -p "$APP_DIR"
  ditto "$SOURCE_ROOT" "$APP_DIR"
fi

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi
PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"
BOSS="$VENV_DIR/bin/boss"

"$PYTHON" -m pip install --upgrade pip
"$PIP" install --upgrade kabi-boss-cli playwright
"$PYTHON" -m playwright install chromium

DROP_DIR="${1:-}"
if [[ -z "$DROP_DIR" ]]; then
  DROP_DIR="$("$PYTHON" "$APP_DIR/local_node/detect_google_drive_drop.py" --interactive)"
fi
DROP_DIR="$(cd "$DROP_DIR" && pwd)"
mkdir -p "$DROP_DIR"

{
  printf 'export JOB_SOURCE_DROP_DIR=%q\n' "$DROP_DIR"
  printf 'export LIEPIN_PROFILE_DIR=%q\n' "$PROFILE_DIR"
  printf 'export JOB_SOURCE_NODE_ROOT=%q\n' "$APP_DIR"
  printf 'export JOB_SOURCE_NODE_VENV=%q\n' "$VENV_DIR"
  printf 'export PATH=%q\n' "$VENV_DIR/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
} > "$ENV_FILE"
chmod 600 "$ENV_FILE"

"$PYTHON" - "$APP_DIR/local_node/config.example.json" "$SUPPORT_DIR/config.mac.json" <<'PY'
import json
import sys
from pathlib import Path
source, target = map(Path, sys.argv[1:])
data = json.loads(source.read_text(encoding="utf-8"))
data["drop_dir"] = ""
data.setdefault("liepin", {})["headless"] = True
target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
PY

export PATH="$VENV_DIR/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export JOB_SOURCE_DROP_DIR="$DROP_DIR"
export LIEPIN_PROFILE_DIR="$PROFILE_DIR"

echo
echo "=== BOSS本地授权 ==="
echo "请在本机完成正常登录。不要把密码、Cookie、Token或二维码发到聊天。"
"$BOSS" login

echo
echo "=== 猎聘本地授权 ==="
"$PYTHON" "$APP_DIR/local_node/authorize_liepin.py"

echo
echo "=== 本地健康检查 ==="
"$PYTHON" "$APP_DIR/validation/source_registry_check.py"
"$PYTHON" "$APP_DIR/validation/source_control_plane.py" health --context local

echo
echo "Mac本地节点首次设置完成。"
echo "应用目录：$APP_DIR"
echo "输出目录：$DROP_DIR"
echo "下一步运行："
echo "  \"$APP_DIR/local_node/run_mac.sh\""
echo "手动采集成功后安装每日07:15任务："
echo "  \"$APP_DIR/local_node/install_mac_launchd.sh\" 07:15"
