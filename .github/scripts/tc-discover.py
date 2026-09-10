# TC3 只读发现 + 显式部署范围校验；绝不默认保活账号下全部工作区。
import os, sys, json, hashlib, hmac, datetime, time, urllib.request, urllib.error
import re


def parse_space_keys(raw):
    """Require an explicit, output-safe allowlist, preserving its order."""
    if not raw or not raw.strip():
        raise ValueError("请在仓库 Actions Variables 中设置 SPACE_KEYS；不自动选择全部工作区")
    keys = [part.strip(" \t") for part in raw.split(",")]
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", key) for key in keys):
        raise ValueError("SPACE_KEYS 必须是逗号分隔的真实 spaceKey，不能含空项、换行或特殊字符")
    return list(dict.fromkeys(keys))


def select_space_keys(rows, requested):
    """Validate every requested key; never silently drop or add a workspace."""
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("DescribeWorkspaces 返回了无效的工作区列表")
    available = set()
    for row in rows:
        # Name 仅兼容旧响应；有 SpaceKey 时不能把显示名误当成 key。
        key = row.get("SpaceKey") or row.get("Name")
        if row.get("Status") != "INVALID" and isinstance(key, str) and key:
            available.add(key)
    unknown = [key for key in requested if key not in available]
    if unknown:
        raise ValueError("SPACE_KEYS 含不存在或已回收的工作区: " + ",".join(unknown))
    return list(requested)


def sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

def sha256hex(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def discover_workspaces():
    sid, skey = os.environ["TENCENT_SECRET_ID"], os.environ["TENCENT_SECRET_KEY"]
    service, host, version = "cloudstudio", "cloudstudio.tencentcloudapi.com", "2023-05-08"
    payload = "{}"

    # 官方 demo 写法
    timestamp = int(time.time())
    dt = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
    date = dt.strftime("%Y-%m-%d")

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
    return rows


def main():
    try:
        # 在调用云 API / 写入输出 / 部署前拒绝空配置和错误格式。
        requested = parse_space_keys(os.environ.get("SPACE_KEYS", ""))
        keys = select_space_keys(discover_workspaces(), requested)
    except ValueError as error:
        print(error)
        return 1
    print("selected workspaces:", keys)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8", newline="\n") as output:
        output.write("space_keys=%s\nfirst=%s\n" % (",".join(keys), keys[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
