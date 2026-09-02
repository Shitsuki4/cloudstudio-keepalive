#!/bin/bash
# 容器完整重建后:只有 /workspace 幸存,重建软链兼容层(所有服务原生跑在 /workspace)
# preview.yml autoOpen 首个调用;幂等,可重复执行
set -e
WS=/workspace/programming-language-demo/apps
mkdir -p /etc/cloudflared /etc/config /opt /var/log

# 二进制软链(仅当缺失或不是软链时替换)
link_bin() {
  [ -e "$2" ] && [ ! -L "$2" ] && rm -f "$2"
  [ -x "$2" ] || ln -sfn "$1" "$2"
}
link_bin $WS/sysbin/cloudflared /usr/local/bin/cloudflared
link_bin $WS/sysbin/cf-probe    /usr/local/bin/cf-probe

# 配置软链(探针 config.conf 期望在 /etc/config/cf-probe)
rm -rf /etc/config/cf-probe;    ln -sfn $WS/sysbin/cf-probe-conf /etc/config/cf-probe
rm -f  /etc/cloudflared/token.txt; ln -sf $WS/sysbin/token.txt   /etc/cloudflared/token.txt

# /opt/new-api 兼容软链
rm -rf /opt/new-api; ln -sfn $WS/new-api /opt/new-api

chmod +x $WS/new-api/*.sh 2>/dev/null || true
echo 'restore-assets: symlinks rebuilt'
