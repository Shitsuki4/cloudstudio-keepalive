# cf-cleanup.py — 删除部署产物:worker script + workers route + DNS record(404-tolerant)
# 仅用于验收前的环境还原;用完即删。输入(env): CF_AUTH_EMAIL, CF_GLOBAL_API_KEY, KEEPALIVE_DOMAIN
import os, sys, json, urllib.request, urllib.error

email = os.environ["CF_AUTH_EMAIL"]
gkey = os.environ["CF_GLOBAL_API_KEY"]
domain = os.environ["KEEPALIVE_DOMAIN"].lstrip("https://").split("/")[0]
API = "https://api.cloudflare.com/client/v4"
SCRIPT = "cloudstudio-keepalive"

def call(method, path):
    req = urllib.request.Request(API + path, method=method, headers={
        "X-Auth-Email": email, "X-Auth-Key": gkey,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, (json.load(r) if r.status != 204 else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}

# 1. 找 zone + account(按 KEEPALIVE_DOMAIN 后缀匹配)
code, zones = call("GET", "/zones?per_page=50")
if code != 200 or not zones.get("success"):
    print("列 zone 失败:", zones); sys.exit(1)
zone = next((z for z in zones["result"] if domain == z["name"] or domain.endswith("." + z["name"])), None)
if not zone:
    print("未找到 %s 所属 zone,跳过 CF 清理" % domain); sys.exit(0)
zone_id, account_id = zone["id"], zone["account"]["id"]
print("zone=%s account=%s" % (zone_id, account_id))

# 2. 删 workers route(pattern 匹配本域)
code, r = call("GET", "/zones/%s/workers/routes" % zone_id)
for route in (r.get("result") or []):
    if domain in (route.get("pattern") or ""):
        d, _ = call("DELETE", "/zones/%s/workers/routes/%s" % (zone_id, route["id"]))
        print("删 route %s (%s): HTTP %d" % (route["id"], route.get("pattern"), d))

# 3. 删 DNS record(name = KEEPALIVE_DOMAIN)
code, r = call("GET", "/zones/%s/dns_records?name=%s" % (zone_id, domain))
for rec in (r.get("result") or []):
    d, _ = call("DELETE", "/zones/%s/dns_records/%s" % (zone_id, rec["id"]))
    print("删 DNS %s (%s -> %s): HTTP %d" % (rec["id"], rec.get("name"), rec.get("content"), d))

# 4. 删 worker script(必须在删 route 之后,否则有 active route 会拒绝)
code, _ = call("DELETE", "/accounts/%s/workers/scripts/%s" % (account_id, SCRIPT))
print("删 worker script %s: HTTP %d" % (SCRIPT, code))

print("CF 清理完成")
