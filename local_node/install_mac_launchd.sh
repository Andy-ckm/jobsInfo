#!/bin/bash
set -euo pipefail

TIME_VALUE="${1:-07:15}"
if [[ ! "$TIME_VALUE" =~ ^([01][0-9]|2[0-3]):([0-5][0-9])$ ]]; then
  echo "时间格式必须为HH:MM，例如07:15。" >&2
  exit 2
fi
HOUR="${TIME_VALUE%%:*}"
MINUTE="${TIME_VALUE##*:}"
HOUR=$((10#$HOUR))
MINUTE=$((10#$MINUTE))

SUPPORT_DIR="$HOME/Library/Application Support/JobSourceNode"
ENV_FILE="$SUPPORT_DIR/node.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "请先运行setup_mac.sh完成首次授权。" >&2
  exit 3
fi
source "$ENV_FILE"

LABEL="com.andyckm.jobsource.localnode"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$SUPPORT_DIR/logs"
RUN_SCRIPT="$JOB_SOURCE_NODE_ROOT/local_node/run_mac.sh"
mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
chmod +x "$RUN_SCRIPT"

xml_escape() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g' -e "s/'/\&apos;/g"
}
RUN_ESCAPED="$(xml_escape "$RUN_SCRIPT")"
HOME_ESCAPED="$(xml_escape "$HOME")"
ENV_ESCAPED="$(xml_escape "$ENV_FILE")"
OUT_ESCAPED="$(xml_escape "$LOG_DIR/launchd.out.log")"
ERR_ESCAPED="$(xml_escape "$LOG_DIR/launchd.err.log")"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$RUN_ESCAPED</string>
    <string>boss,liepin</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>$HOME_ESCAPED</string>
    <key>JOB_SOURCE_NODE_ENV</key><string>$ENV_ESCAPED</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$HOUR</integer>
    <key>Minute</key><integer>$MINUTE</integer>
  </dict>
  <key>ProcessType</key><string>Background</string>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$OUT_ESCAPED</string>
  <key>StandardErrorPath</key><string>$ERR_ESCAPED</string>
</dict>
</plist>
PLIST

plutil -lint "$PLIST"
DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl enable "$DOMAIN/$LABEL"

echo "Mac每日采集任务已安装：$TIME_VALUE"
echo "任务：$LABEL"
echo "配置：$PLIST"
echo "立即测试：launchctl kickstart -k $DOMAIN/$LABEL"
echo "查看状态：launchctl print $DOMAIN/$LABEL"
echo "日志：$LOG_DIR"
