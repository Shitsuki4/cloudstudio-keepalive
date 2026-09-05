import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


def digest(content):
    return hashlib.sha256(content).hexdigest()


def atomic_write(path, content, mode):
    descriptor, temporary = tempfile.mkstemp(prefix=".keepalive-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(root, bundle):
    root = Path(root).absolute()
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("Workspace must be an existing real directory")
    managed = root / ".keepalive"
    preview_dir = root / ".vscode"
    for directory in (managed, preview_dir, managed / "backups", managed / "start.d", managed / "logs"):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise RuntimeError(f"Unsafe startup directory: {directory.name}")
    for directory in (managed, preview_dir, managed / "backups", managed / "start.d", managed / "logs"):
        directory.mkdir(mode=0o700, exist_ok=True)
    state_path = managed / "install-state.json"
    targets = {"boot.sh": managed / "boot.sh", "preview.yml": preview_dir / "preview.yml"}
    if state_path.is_symlink():
        raise RuntimeError("Refusing symlinked installation state")
    previous = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    previous_hashes = previous.get("hashes", {})
    incoming = {name: base64.b64decode(bundle[name], validate=True) for name in targets}
    preserve_preview = False
    for name, target in targets.items():
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise RuntimeError(f"Unsafe startup target: {name}")
        if not target.exists():
            continue
        current = digest(target.read_bytes())
        if current == digest(incoming[name]):
            continue
        if name == "preview.yml" and current != previous_hashes.get(name):
            preserve_preview = True
            continue
        if current != previous_hashes.get(name):
            raise RuntimeError(f"Refusing to overwrite unmanaged or edited {name}")
    backup_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = managed / "backups" / backup_id
    backup.mkdir(mode=0o700)
    hashes = {}
    for name, target in targets.items():
        if target.exists():
            shutil.copy2(target, backup / name)
        if name == "preview.yml" and preserve_preview:
            print("Existing preview.yml preserved; native lifecycle does not require replacing it")
            continue
        atomic_write(target, incoming[name], 0o700 if name.endswith(".sh") else 0o600)
        if digest(target.read_bytes()) != digest(incoming[name]):
            raise RuntimeError(f"Verification failed: {name}")
        hashes[name] = digest(incoming[name])
    state = {"version": 1, "hashes": hashes, "backup": backup_id}
    atomic_write(state_path, (json.dumps(state, indent=2) + "\n").encode("utf-8"), 0o600)
    print("KEEPALIVE_INSTALL_OK")


if __name__ == "__main__":
    try:
        install("/workspace", json.loads(base64.b64decode(sys.argv[1], validate=True)))
    except Exception as error:
        print(f"Startup installation failed: {error}", file=sys.stderr)
        sys.exit(1)
