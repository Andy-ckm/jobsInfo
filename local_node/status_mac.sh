#!/bin/bash
set -u
LABEL="com.andyckm.jobsource.localnode"
DOMAIN="gui/$(id -u)"
SUPPORT_DIR="$HOME/Library/Application Support/JobSourceNode"
ENV_FILE="$SUPPORT_DIR/node.env"

echo "=== launchd状态 ==="
launchctl print "$DOMAIN/$LABEL" 2>/dev/null || echo "任务尚未安装或当前未登录GUI会话。"

echo
echo "=== 本地配置 ==="
if [[ -f "$ENV_FILE" ]]; then
  grep -E '^export (JOB_SOURCE_DROP_DIR|LIEPIN_PROFILE_DIR|JOB_SOURCE_NODE_ROOT)=' "$ENV_FILE"
else
  echo "未找到$ENV_FILE"
fi

echo
echo "=== 最新日志 ==="
tail -n 40 "$SUPPORT_DIR/logs/launchd.out.log" 2>/dev/null || true
tail -n 40 "$SUPPORT_DIR/logs/launchd.err.log" 2>/dev/null || true
