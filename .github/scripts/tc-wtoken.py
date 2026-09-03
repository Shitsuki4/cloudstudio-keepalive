# tc-wtoken.py — 用腾讯云密钥(TC3 签名)给指定 spaceKey 铸 workspace token
# 用途:open-ide.js 用它驱动 tty 页面(免 browserless、免 SSH accessToken)
# 输入(env): TENCENT_SECRET_ID, TENCENT_SECRET_KEY;argv[1] = spaceKey(缺省取第一个工作区)
# 输出(GITHUB_OUTPUT): token, space
import os, sys, json, hashlib, hmac, datetime, time, urllib.request, urllib.error

sid, skey = os.environ["TENCENT_SECRET_ID"], os.environ["TENCENT_SECRET_KEY"]
service, host, version = "cloudstudio", "cloudstudio.tencentcloudapi.com", "2023-05-08"

space = sys.argv[1] if len(sys.argv) > 1 else ""

def sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

def sha256hex(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def call(action, payload):
    timestamp = int(time.time())
    date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    ct = "application/json; charset=utf-8"
    canonical_headers = "content-type:%s\nhost:%s\n" % (ct, host)
    canonical_request = "%s\n%s\n%s\n%s\n%s\n%s" % ("POST", "/", "", canonical_headers, "content-type;host", sha256hex(payload))
    credential_scope = "%s/%s/tc3_request" % (date, service)
    string_to_sign = "TC3-HMAC-SHA256\n%s\n%s\n%s" % (timestamp, credential_scope, sha256hex(canonical_request))
    secret_signing = sign(sign(sign(("TC3" + skey).encode("utf-8"), date), service), "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = "TC3-HMAC-SHA256 Credential=%s/%s, SignedHeaders=content-type;host, Signature=%s" % (sid, credential_scope, signature)
    req = urllib.request.Request("https://" + host, data=payload.encode("utf-8"), headers={
        "Authorization": authorization, "Content-Type": ct, "Host": host,
        "X-TC-Action": action, "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": version, "X-TC-Region": "ap-shanghai"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        print("HTTP %d: %s" % (e.code, e.read().decode()[:500])); sys.exit(1)

# 没给 spaceKey 就自动发现第一个
if not space:
    d = call("DescribeWorkspaces", "{}")
    rows = d.get("Response", {}).get("Data") or []
    if not rows:
        print("账号下没有工作区"); sys.exit(1)
    space = rows[0].get("SpaceKey") or rows[0].get("Name")
    print("auto space:", space)

d = call("CreateWorkspaceToken", json.dumps({"SpaceKey": space}))
err = d.get("Response", {}).get("Error")
if err:
    print("CreateWorkspaceToken error:", err); sys.exit(1)
tok = d["Response"]["Token"]
with open(os.environ["GITHUB_OUTPUT"], "a") as f:
    f.write("token=%s\nspace=%s\n" % (tok, space))
print("minted token for %s (expire %s)" % (space, d["Response"].get("ExpiredTime")))
