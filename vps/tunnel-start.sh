#!/bin/bash
# cloudflared tunnel 自启(主运行目录 /workspace)
WS=/workspace/programming-language-demo/apps
TOKEN=$(cat $WS/sysbin/token.txt)
if pgrep -x cloudflared >/dev/null; then
  echo 'cloudflared already running, skip'
  exit 0
fi
setsid nohup $WS/sysbin/cloudflared tunnel --no-autoupdate run --token "$TOKEN" >> /tmp/cloudflared.log 2>&1 < /dev/null &
echo 'cloudflared started'
