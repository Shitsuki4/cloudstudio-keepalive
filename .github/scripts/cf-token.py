# cf-token.py — 用 Global API Key 铸一个最小权限临时 token + 自动发现 account/zone
# 输入(env): CF_AUTH_EMAIL, CF_GLOBAL_API_KEY, KEEPALIVE_DOMAIN
# 输出(GITHUB_OUTPUT): token, account_id, zone_id, token_id
import os, sys, json, urllib.request, urllib.error

email = os.environ["CF_AUTH_EMAIL"]
gkey = os.environ["CF_GLOBAL_API_KEY"]
domain = os.environ["KEEPALIVE_DOMAIN"].lstrip("https://").split("/")[0]
API = "https://api.cloudflare.com/client/v4"

def call(method, path, body=None, auth="global"):
    if auth == "global":
        headers = {"X-Auth-Email": email, "X-Auth-Key": gkey}
    else:
        headers = {"Authorization": "Bearer " + auth}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        print("HTTP %d %s: %s" % (e.code, path, e.read().decode()[:500])); sys.exit(1)

# 1. 列 zone,找 KEEPALIVE_DOMAIN 所属 zone + 所属 account
zones = call("GET", "/zones?per_page=50")
if not zones.get("success"): print(zones); sys.exit(1)
zone = next((z for z in zones["result"] if domain == z["name"] or domain.endswith("." + z["name"])), None)
if not zone:
    print("未找到 %s 所属 zone(现有关联 zones: %s)" % (domain, [z["name"] for z in zones["result"]])); sys.exit(1)
zone_id, account_id, zone_name = zone["id"], zone["account"]["id"], zone["name"]
print("zone: %s (%s)  account: %s (%s)" % (zone_name, zone_id, account_id, zone["account"]["name"]))

# 2. 找权限组 id
groups = call("GET", "/user/tokens/permission_groups")["result"]
def gid(name):
    g = next((g for g in groups if g["name"] == name), None)
    if not g: print("permission group not found: " + name); sys.exit(1)
    return {"id": g["id"]}
acc_groups = [gid("Workers Scripts Write"), gid("Account Settings Read")]
zone_groups = [gid("DNS Write"), gid("Workers Routes Write")]

# 3. 铸 token
body = {
    "name": "gh-keepalive-deploy-%s" % zone_name,
    "policies": [
        {"effect": "allow", "resources": {"com.cloudflare.api.account." + account_id: "*"}, "permission_groups": acc_groups},
        {"effect": "allow", "resources": {"com.cloudflare.api.account.zone." + zone_id: "*"}, "permission_groups": zone_groups},
    ],
}
r = call("POST", "/user/tokens", body)
if not r.get("success"): print(r); sys.exit(1)
tok = r["result"]

with open(os.environ["GITHUB_OUTPUT"], "a") as f:
    f.write("token=%s\naccount_id=%s\nzone_id=%s\ntoken_id=%s\n" % (tok["value"], account_id, zone_id, tok["id"]))
print("minted token %s (id %s)" % ("*" * 12 + tok["value"][-6:], tok["id"]))
