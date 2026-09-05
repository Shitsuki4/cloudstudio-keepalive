# 对照官方 TC3 规范重写签名(与官方 python demo 结构完全一致)
import os, sys, json, hashlib, hmac, datetime, time, urllib.request, urllib.error

sid, skey = os.environ["TENCENT_SECRET_ID"], os.environ["TENCENT_SECRET_KEY"]
service, host, version = "cloudstudio", "cloudstudio.tencentcloudapi.com", "2023-05-08"
payload = "{}"

# 官方 demo 写法
timestamp = int(time.time())
dt = datetime.datetime.utcnow()
date = dt.strftime("%Y-%m-%d")

def sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

def sha256hex(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

# 步骤1:拼 canonical request
http_method = "POST"
canonical_uri = "/"
canonical_qs = ""
ct = "application/json; charset=utf-8"
canonical_headers = "content-type:%s\nhost:%s\n" % (ct, host)
signed_headers = "content-type;host"
hashed_payload = sha256hex(payload)
canonical_request = "%s\n%s\n%s\n%s\n%s\n%s" % (http_method, canonical_uri, canonical_qs, canonical_headers, signed_headers, hashed_payload)

# 步骤2:拼 string to sign
algorithm = "TC3-HMAC-SHA256"
credential_scope = "%s/%s/tc3_request" % (date, service)
hashed_canonical = sha256hex(canonical_request)
string_to_sign = "%s\n%s\n%s\n%s" % (algorithm, timestamp, credential_scope, hashed_canonical)

# 步骤3:签名
secret_date = sign(("TC3" + skey).encode("utf-8"), date)
secret_service = sign(secret_date, service)
secret_signing = sign(secret_service, "tc3_request")
signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

authorization = "%s Credential=%s/%s, SignedHeaders=%s, Signature=%s" % (algorithm, sid, credential_scope, signed_headers, signature)

req = urllib.request.Request("https://" + host, data=payload.encode("utf-8"), headers={
    "Authorization": authorization,
    "Content-Type": ct, "Host": host,
    "X-TC-Action": "DescribeWorkspaces", "X-TC-Timestamp": str(timestamp),
    "X-TC-Version": version, "X-TC-Region": "ap-shanghai"})

# 腾讯云 API 偶发超时(出口网络抖动),重试 4 次
last = None
for attempt in range(4):
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        break
    except urllib.error.HTTPError as e:
        print("HTTP %d: %s" % (e.code, e.read().decode()[:800])); sys.exit(1)
    except Exception as e:
        last = e
        print("NET: %s (attempt %d/4)" % (e, attempt + 1))
        time.sleep(5)
else:
    print("NET: %s" % last); sys.exit(2)

if d.get("Response", {}).get("Error"):
    print("TencentCloud Error:", d["Response"]["Error"]); sys.exit(1)

rows = d["Response"].get("Data") or d["Response"].get("WorkspaceList") or []
if rows and isinstance(rows, dict): rows = rows.get("WorkspaceList", [])
# 过滤已回收(INVALID)的工作区:DescribeWorkspaces 会列出已删除的幽灵工作区,
# 拿去 RunWorkspace/heartbeat 会报 "Workspace had been removed"
dead = [w.get("SpaceKey") or w.get("Name") for w in rows if w.get("Status") == "INVALID"]
if dead:
    print("跳过已回收(INVALID)工作区:", dead)
rows = [w for w in rows if w.get("Status") != "INVALID"]
# 注意:Name 是工作区显示名(如 "free"),SpaceKey 才是 API 用的真实 key —— 必须优先 SpaceKey
keys = [w.get("SpaceKey") or w.get("Name") for w in rows]
print("status:", {w.get("SpaceKey") or w.get("Name"): w.get("Status") for w in rows})
print("workspaces:", keys)
if not keys:
    print("该账号下没有 CloudStudio 工作区——请先到 https://ide.cloud.tencent.com 创建"); sys.exit(1)
with open(os.environ["GITHUB_OUTPUT"], "a") as f:
    f.write("space_keys=%s\nfirst=%s\n" % (",".join(keys), keys[0]))
