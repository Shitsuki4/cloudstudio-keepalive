#!/bin/bash
# 自愈守护:每分钟检查服务;每小时 SQLite 热备到同卷 .bak(防进程崩溃丢最近写入)
NA=/workspace/programming-language-demo/apps/new-api
WS=/workspace/programming-language-demo/apps
LAST_BAK=0
while true; do
  if [ -d /workspace/programming-language-demo/apps/new-api ]; then
    pgrep -x new-api     >/dev/null || bash $NA/boot-all.sh     >> /tmp/selfheal.log 2>&1
    pgrep -x cloudflared >/dev/null || bash $NA/tunnel-start.sh >> /tmp/selfheal.log 2>&1
    pgrep -x cf-probe    >/dev/null || bash $NA/probe-start.sh  >> /tmp/selfheal.log 2>&1
    NOW=$(date +%s)
    if [ $((NOW - LAST_BAK)) -ge 3600 ] && [ -f $NA/data/one-api.db ]; then
      cp $NA/data/one-api.db $NA/data/one-api.db.bak 2>/dev/null || true
      LAST_BAK=$NOW
    fi
  fi
  sleep 60
done
