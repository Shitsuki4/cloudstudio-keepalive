#!/bin/bash
# new-api 自启(主运行目录 /workspace,preview.yml autoOpen 调用)
NA=/workspace/programming-language-demo/apps/new-api
if pgrep -x new-api > /dev/null; then
  echo 'new-api already running, skip'
  exit 0
fi
cd $NA/data || exit 1
mkdir -p $NA/logs
TZ=Asia/Shanghai nohup $NA/new-api --port 3000 --log-dir $NA/logs > /tmp/new-api.log 2>&1 < /dev/null &
echo 'new-api started'
