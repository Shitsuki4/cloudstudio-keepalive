# push-via-api.py — git push 被重置时,通过 GitHub Contents API 逐文件上传
# 用法: python push-via-api.py <repo> <branch> <local_root> <token>
import os, sys, base64, json, time
try:
    import urllib.request as rq
except ImportError:
    sys.exit("need py3")

repo, branch, root, token = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
API = "https://api.github.com"

def call(method, url, data=None):
    req = rq.Request(url, method=method, data=json.dumps(data).encode() if data else None, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "push-script",
    })
    try:
        with rq.urlopen(req, timeout=30) as r:
            body = r.read().decode()
            return r.status, json.loads(body) if body else {}
    except Exception as e:
        body = getattr(e, "read", lambda: b"")()
        try: detail = json.loads(body.decode())
        except Exception: detail = {"raw": str(body[:200])}
        return getattr(e, "code", 0), detail

# 1. 确保 branch 存在(空仓库直接 PUT 第一个文件即可)
code, ref = call("GET", f"{API}/repos/{repo}/git/ref/heads/{branch}")
if code != 200:
    print(f"branch {branch} 不存在,将随首个文件自动创建")

files = []
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", ".wrangler")]
    for fn in filenames:
        full = os.path.join(dirpath, fn)
        rel = os.path.relpath(full, root).replace(os.sep, "/")
        if rel == ".gitignore" and "api-push" not in rel: pass
        files.append((rel, full))
files.sort()
print(f"共 {len(files)} 个文件")

ok = fail = skip = 0
for rel, full in files:
    with open(full, "rb") as f:
        content = base64.b64encode(f.read()).decode()
    code, cur = call("GET", f"{API}/repos/{repo}/contents/{rel}?ref={branch}")
    sha = cur.get("sha") if code == 200 else None
    if sha:
        import hashlib
        with open(full, "rb") as f:
            local_sha = base64.b64encode(hashlib.sha1(b"blob " + str(os.path.getsize(full)).encode() + b"\0" + f.read()).digest()).decode()
        if local_sha == sha:
            skip += 1
            continue
    for attempt in range(3):
        code, res = call("PUT", f"{API}/repos/{repo}/contents/{rel}", {
            "message": f"upload {rel}",
            "content": content,
            "branch": branch,
            **({"sha": sha} if sha else {}),
        })
        if code in (200, 201):
            print(f"  OK  {rel}")
            ok += 1
            break
        time.sleep(2 * (attempt + 1))
    else:
        print(f"  FAIL {rel}: {res}")
        fail += 1

print(f"\n完成: ok={ok} skip={skip} fail={fail}")
sys.exit(1 if fail else 0)
