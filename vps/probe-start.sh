#!/bin/bash
# cf-probe(CF-Server-Monitor 探针)自启;不用监控面板就删掉这个脚本和 boot 链里的引用
WS=/workspace/programming-language-demo/apps
if pgrep -x cf-probe >/dev/null; then
  echo 'cf-probe already running, skip'
  exit 0
fi
nohup $WS/sysbin/cf-probe run >> /var/log/cf-probe.log 2>&1 < /dev/null &
echo 'cf-probe started'
