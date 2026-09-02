#!/bin/bash
# new-api 自启(主运行目录 /workspace,preview.yml autoOpen 调用)
NA=/workspace/programming-language-demo/apps/new-api
if [ ! -x $NA/new-api ]; then
  echo 'new-api binary 不存在 — 新工作区跳过(应用就位后 selfheal 会自动拉起)'
  exit 0
fi
if pgrep -x new-api > /dev/null; then
  echo 'new-api already running, skip'
  exit 0
fi
cd $NA/data || { mkdir -p $NA/data && cd $NA/data; }
mkdir -p $NA/logs
TZ=Asia/Shanghai nohup $NA/new-api --port 3000 --log-dir $NA/logs > /tmp/new-api.log 2>&1 < /dev/null &
echo 'new-api started'
