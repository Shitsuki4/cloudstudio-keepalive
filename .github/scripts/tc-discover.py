# tc-discover.py — 用腾讯云密钥列出 CloudStudio 工作区 spaceKey(自动发现 SPACE_KEYS)
# 输入(env): TENCENT_SECRET_ID, TENCENT_SECRET_KEY
# 输出(GITHUB_OUTPUT): space_keys(逗号分隔), first(第一个 key)
import os, sys, json, hashlib, hmac, datetime, urllib.request

sid, skey = os.environ["TENCENT_SECRET_ID"], os.environ["TENCENT_SECRET_KEY"]
service, host, version = "cloudstudio", "cloudstudio.tencentcloudapi.com", "2023-05-08"
payload = json.dumps({"PageNumber": 1, "PageSize": 50})
ts = int(datetime.datetime.utcnow().timestamp())
date = datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")

def hsha(key, msg): return hmac.new(key, msg.encode(), hashlib.sha256).digest()
def sha(s): return hashlib.sha256(s.encode()).hexdigest()

canonical = "POST\n/\n\ncontent-type:application/json; charset=utf-8\nhost:%s\ncontent-type;host\n%s" % (host, sha(payload))
scope = "%s/%s/tc3_request" % (date, service)
sts = "TC3-HMAC-SHA256\n%d\n%s\n%s" % (ts, scope, sha(canonical))
secret = hsha(hsha(hsha(("TC3" + skey).encode(), date), service), b"tc3_request")
sig = hmac.new(secret, sts.encode(), hashlib.sha256).hexdigest()

req = urllib.request.Request("https://" + host, data=payload.encode(), headers={
    "Content-Type": "application/json; charset=utf-8", "Host": host,
    "X-TC-Action": "DescribeWorkspaces", "X-TC-Timestamp": str(ts), "X-TC-Version": version,
    "X-TC-Region": "ap-shanghai",
    "Authorization": "TC3-HMAC-SHA256 Credential=%s/%s, SignedHeaders=content-type;host, Signature=%s" % (sid, scope, sig)})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
except urllib.error.HTTPError as e:
    print("HTTP %d: %s" % (e.code, e.read().decode()[:800])); sys.exit(1)
if d.get("Response", {}).get("Error"):
    print("TencentCloud Error:", d["Response"]["Error"]); sys.exit(1)

rows = d["Response"].get("WorkspaceList") or d["Response"].get("Data") or []
if rows and isinstance(rows, dict): rows = rows.get("WorkspaceList", [])
keys = [w.get("Name") or w.get("SpaceKey") for w in rows]
print("workspaces:", json.dumps([{ "key": k } for k in keys]))
if not keys:
    print("该账号下没有 CloudStudio 工作区——请先到 https://ide.cloud.tencent.com 创建一个"); sys.exit(1)
with open(os.environ["GITHUB_OUTPUT"], "a") as f:
    f.write("space_keys=%s\nfirst=%s\n" % (",".join(keys), keys[0]))
