# 用 GitHub API 写 Actions Secret(libsodium 加密)
# 用法: python .set-secret.py <owner/repo> <secret-name> <secret-value>
# 依赖 PyNaCl(已装)。token 从 git credential store 取。
import base64
import json
import subprocess
import sys
import urllib.request

from nacl import encoding, public

repo, name, value = sys.argv[1], sys.argv[2], sys.argv[3]
tok = subprocess.run(
    ["git", "credential", "fill"],
    input="protocol=https\nhost=github.com\n\n",
    capture_output=True,
    text=True,
).stdout
token = next(l.split("=", 1)[1] for l in tok.splitlines() if l.startswith("password="))
hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

req = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/actions/secrets/public-key", headers=hdr
)
key = json.load(urllib.request.urlopen(req))
pk = public.PublicKey(key["key"], encoding.Base64Encoder())
sealed = public.SealedBox(pk).encrypt(value.encode())

req = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/actions/secrets/{name}",
    data=json.dumps(
        {
            "encrypted_value": base64.b64encode(sealed).decode(),
            "key_id": key["key_id"],
        }
    ).encode(),
    headers={**hdr, "Content-Type": "application/json"},
    method="PUT",
)
print(req.full_url, urllib.request.urlopen(req).status)
