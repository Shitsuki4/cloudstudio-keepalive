# cf-cleanup.py — 全清重装验收用:删除本部署在 CF 侧的全部产物(worker script / workers route / DNS 记录)
# 输入(env): CF_AUTH_EMAIL, CF_GLOBAL_API_KEY, KEEPALIVE_DOMAIN
# 用 Global Key 认证(和 cf-token.py 同源),幂等:资源不存在(404)不算失败
import os, sys, json, urllib.request, urllib.error

email = os.environ["CF_AUTH_EMAIL"]
gkey = os.environ["CF_GLOBAL_API_KEY"]
domain = os.environ["KEEPALIVE_DOMAIN"].lstrip("https://").split("/")[0]
API = "https://api.cloudflare.com/client/v4"
WORKER_NAME = "cloudstudio-keepalive"

def call(method, path, body=None):
    headers = {"X-Auth-Email": email, "X-Auth-Key": gkey}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        b = e.read().decode()
        if e.code == 404:
            return {"success": True, "not_found": True}
        print("HTTP %d %s: %s" % (e.code, path, b[:500]))
        return {"success": False, "body": b}

# 1. 找 zone
zones = call("GET", "/zones?per_page=50")
if not zones.get("success"):
    print(zones); sys.exit(1)
zone = next((z for z in zones["result"] if domain == z["name"] or domain.endswith("." + z["name"])), None)
if not zone:
    print("zone not found for %s (zones: %s)" % (domain, [z["name"] for z in zones["result"]])); sys.exit(1)
zone_id, account_id = zone["id"], zone["account"]["id"]
print("zone %s (%s) account %s" % (zone["name"], zone_id, account_id))

# 2. 删 workers route
routes = call("GET", "/zones/%s/workers/routes" % zone_id).get("result", [])
for r in routes:
    if domain in r.get("pattern", ""):
        d = call("DELETE", "/zones/%s/workers/routes/%s" % (zone_id, r["id"]))
        print("route %s -> deleted=%s" % (r["pattern"], d.get("success")))
if not any(domain in r.get("pattern", "") for r in routes):
    print("no route for %s" % domain)

# 3. 删 DNS 记录
dns = call("GET", "/zones/%s/dns_records?name=%s" % (zone_id, domain)).get("result", [])
for rec in dns:
    d = call("DELETE", "/zones/%s/dns_records/%s" % (zone_id, rec["id"]))
    print("dns %s (%s %s) -> deleted=%s" % (rec["name"], rec["type"], rec["content"], d.get("success")))
if not dns:
    print("no dns record for %s" % domain)

# 4. 删 worker script
d = call("DELETE", "/accounts/%s/workers/scripts/%s" % (account_id, WORKER_NAME))
print("worker %s -> deleted=%s" % (WORKER_NAME, d.get("success")))

print("CF_CLEANUP_DONE")
