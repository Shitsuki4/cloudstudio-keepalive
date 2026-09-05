import datetime
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request


def call(action, payload):
    secret_id = os.environ["TENCENT_SECRET_ID"]
    secret_key = os.environ["TENCENT_SECRET_KEY"]
    host = "cloudstudio.tencentcloudapi.com"
    content_type = "application/json; charset=utf-8"
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    timestamp = int(time.time())
    date = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).strftime("%Y-%m-%d")
    scope = f"{date}/cloudstudio/tc3_request"
    canonical = "\n".join([
        "POST", "/", "",
        f"content-type:{content_type}\nhost:{host}\n",
        "content-type;host", hashlib.sha256(body).hexdigest(),
    ])
    signing_text = "\n".join([
        "TC3-HMAC-SHA256", str(timestamp), scope,
        hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    ])
    signing_key = ("TC3" + secret_key).encode("utf-8")
    for part in (date, "cloudstudio", "tc3_request"):
        signing_key = hmac.new(signing_key, part.encode("utf-8"), hashlib.sha256).digest()
    signature = hmac.new(signing_key, signing_text.encode("utf-8"), hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        f"https://{host}", data=body, method="POST", headers={
            "Content-Type": content_type,
            "Authorization": (
                f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, "
                f"SignedHeaders=content-type;host, Signature={signature}"
            ),
            "X-TC-Action": action,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": "2023-05-08",
            "X-TC-Region": os.environ.get("TENCENT_REGION", "ap-shanghai"),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.load(response).get("Response")
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"{action}: HTTP {error.code}") from None
    if not isinstance(result, dict):
        raise RuntimeError(f"{action}: invalid API response")
    if result.get("Error"):
        error = result["Error"]
        message = str(error.get("Message", "")).replace(secret_id, "[redacted]").replace(secret_key, "[redacted]")
        raise RuntimeError(f"{action}: {error.get('Code')}: {message}")
    return result


def workspaces():
    result = call("DescribeWorkspaces", {})
    rows = result.get("Data") or result.get("WorkspaceList") or []
    if isinstance(rows, dict):
        rows = rows.get("WorkspaceList", [])
    if not isinstance(rows, list):
        raise RuntimeError("DescribeWorkspaces: invalid workspace list")
    return rows
