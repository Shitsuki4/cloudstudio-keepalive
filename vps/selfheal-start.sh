#!/bin/bash
# selfheal 守护自启(带防重入)
NA=/workspace/programming-language-demo/apps/new-api
if pgrep -f "bash $NA/selfheal.sh" >/dev/null; then
  echo 'selfheal already running, skip'
  exit 0
fi
setsid nohup /bin/bash $NA/selfheal.sh >> /tmp/selfheal.log 2>&1 < /dev/null &
echo 'selfheal started'
