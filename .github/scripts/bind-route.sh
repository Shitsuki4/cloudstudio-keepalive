#!/bin/bash
# 给 worker 绑自定义域路由:DNS A 记录(proxied)+ workers route
# workers.dev 在国内被 DNS 污染,必须走自定义域
set -e
: "${CF_API_TOKEN:?}"; : "${CF_ZONE_ID:?}"; : "${CF_ACCOUNT_ID:?}"; : "${KEEPALIVE_DOMAIN:?}"

API=https://api.cloudflare.com/client/v4
H1="Authorization: Bearer $CF_API_TOKEN"
H2="Content-Type: application/json"
ZONE="$API/zones/$CF_ZONE_ID"
SUBDOMAIN="${KEEPALIVE_DOMAIN%%.*}"           # keepalive
DOMAIN="${KEEPALIVE_DOMAIN#*.}"               # example.com
echo "zone=$CF_ZONE_ID sub=$SUBDOMAIN domain=$DOMAIN"

# 1. 已有同名 DNS 记录则取 id(兼容 A / CNAME)
REC_ID=$(curl -s -H "$H1" "$ZONE/dns_records?name=$KEEPALIVE_DOMAIN" | python3 -c "import sys,json;d=json.load(sys.stdin);r=d.get('result') or [];print(r[0]['id'] if r else '')")
if [ -n "$REC_ID" ]; then
  echo "DNS 记录已存在($REC_ID),改为 proxied 指向 192.0.2.1"
  curl -s -X PUT -H "$H1" -H "$H2" "$ZONE/dns_records/$REC_ID" \
    --data "{\"type\":\"A\",\"name\":\"$SUBDOMAIN\",\"content\":\"192.0.2.1\",\"proxied\":true}" | python3 -c "import sys,json;d=json.load(sys.stdin);print('PUT dns:',d.get('success'),d['errors'])"
else
  echo "新建 proxied A 记录 $SUBDOMAIN -> 192.0.2.1"
  curl -s -X POST -H "$H1" -H "$H2" "$ZONE/dns_records" \
    --data "{\"type\":\"A\",\"name\":\"$SUBDOMAIN\",\"content\":\"192.0.2.1\",\"proxied\":true}" | python3 -c "import sys,json;d=json.load(sys.stdin);print('POST dns:',d.get('success'),d['errors'])"
fi

# 2. workers route
PATTERN="$KEEPALIVE_DOMAIN/*"
ROUTE_ID=$(curl -s -H "$H1" "$ZONE/workers/routes?filter\[pattern\]=${PATTERN//\*/%2A}" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);r=d.get('result') or [];print(r[0]['id'] if r else '')")
if [ -n "$ROUTE_ID" ]; then
  echo "route 已存在($ROUTE_ID),更新指向 worker"
  curl -s -X PUT -H "$H1" -H "$H2" "$ZONE/workers/routes/$ROUTE_ID" \
    --data "{\"pattern\":\"$PATTERN\",\"script\":\"cloudstudio-keepalive\"}" | python3 -c "import sys,json;d=json.load(sys.stdin);print('PUT route:',d.get('success'),d['errors'])"
else
  echo "新建 route $PATTERN -> cloudstudio-keepalive"
  curl -s -X POST -H "$H1" -H "$H2" "$ZONE/workers/routes" \
    --data "{\"pattern\":\"$PATTERN\",\"script\":\"cloudstudio-keepalive\"}" | python3 -c "import sys,json;d=json.load(sys.stdin);print('POST route:',d.get('success'),d['errors'])"
fi
echo DONE
