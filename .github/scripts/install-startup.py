import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


BUNDLE_NAMES = ("boot.sh", "supervisor-start.sh", "preview.yml", "keepalive-boot.conf")
TARGET_NAMES = ("boot.sh", "supervisor-start.sh", "preview.yml", "keepalive-boot.conf", "supervisor-include.conf")


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


def read_state(path):
    if path.is_symlink():
        raise RuntimeError("Refusing symlinked installation state")
    if not path.exists():
        return {}
    if not path.is_file():
        raise RuntimeError("Installation state is not a regular file")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Invalid installation state: {error}") from error
    if not isinstance(state, dict) or not isinstance(state.get("hashes", {}), dict):
        raise RuntimeError("Invalid installation state")
    return state


def snapshot(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError(f"Unsafe startup target: {path.name}")
    if not path.exists():
        return {"exists": False, "mode": None, "content": b""}
    stat = path.stat()
    return {"exists": True, "mode": stat.st_mode & 0o777, "content": path.read_bytes()}


def restore_snapshot(path, saved):
    if saved["exists"]:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise RuntimeError(f"Cannot restore unsafe startup target: {path.name}")
        atomic_write(path, saved["content"], saved["mode"])
        return
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError(f"Cannot remove unsafe startup target: {path.name}")
    if path.exists():
        path.unlink()


def write_backup(backup, targets, saved_targets, saved_state):
    backup.mkdir(mode=0o700)
    target_metadata = {}
    for name, target in targets.items():
        saved = saved_targets[name]
        target_metadata[name] = {"exists": saved["exists"], "mode": saved["mode"]}
        if saved["exists"]:
            path = backup / name
            path.write_bytes(saved["content"])
            os.chmod(path, saved["mode"])
    state_metadata = {"exists": saved_state["exists"], "mode": saved_state["mode"]}
    if saved_state["exists"]:
        path = backup / "install-state.json"
        path.write_bytes(saved_state["content"])
        os.chmod(path, saved_state["mode"])
    manifest = {"version": 1, "targets": target_metadata, "state": state_metadata}
    manifest_path = backup / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.chmod(manifest_path, 0o600)


def target_paths(root, supervisor_dir):
    managed = root / ".keepalive"
    return {
        "boot.sh": managed / "boot.sh",
        "supervisor-start.sh": managed / "supervisor-start.sh",
        "preview.yml": root / ".vscode" / "preview.yml",
        "keepalive-boot.conf": managed / "keepalive-boot.conf",
        "supervisor-include.conf": supervisor_dir / "keepalive-boot.conf",
    }


def validate_directories(root, managed, preview_dir, supervisor_dir):
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("Workspace must be an existing real directory")
    for directory in (managed, preview_dir, managed / "backups", managed / "start.d", managed / "logs"):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise RuntimeError(f"Unsafe startup directory: {directory.name}")
    if supervisor_dir.is_symlink() or not supervisor_dir.is_dir():
        raise RuntimeError("Supervisor include directory is unavailable or symlinked")
    if supervisor_dir.parent.is_symlink() or supervisor_dir.parent.parent.is_symlink():
        raise RuntimeError("Supervisor include path contains a symlink")
    for directory in (managed, preview_dir, managed / "backups", managed / "start.d", managed / "logs"):
        directory.mkdir(mode=0o700, exist_ok=True)


def install(root, bundle, supervisor_root="/usr/local/share/supervisor"):
    root = Path(root).absolute()
    managed = root / ".keepalive"
    preview_dir = root / ".vscode"
    supervisor_dir = Path(supervisor_root).absolute()
    validate_directories(root, managed, preview_dir, supervisor_dir)

    state_path = managed / "install-state.json"
    saved_state = snapshot(state_path)
    previous = read_state(state_path)
    previous_hashes = previous.get("hashes", {})
    targets = target_paths(root, supervisor_dir)
    decoded = {name: base64.b64decode(bundle[name], validate=True) for name in BUNDLE_NAMES}
    incoming = {
        "boot.sh": decoded["boot.sh"],
        "supervisor-start.sh": decoded["supervisor-start.sh"],
        "preview.yml": decoded["preview.yml"],
        "keepalive-boot.conf": decoded["keepalive-boot.conf"],
        "supervisor-include.conf": decoded["keepalive-boot.conf"],
    }
    saved_targets = {name: snapshot(targets[name]) for name in TARGET_NAMES}

    preserve_preview = False
    for name in TARGET_NAMES:
        saved = saved_targets[name]
        if not saved["exists"]:
            continue
        current = digest(saved["content"])
        incoming_hash = digest(incoming[name])
        if current == incoming_hash:
            continue
        if name == "preview.yml" and current != incoming_hash:
            preserve_preview = True
            continue
        if current != previous_hashes.get(name):
            raise RuntimeError(f"Refusing to overwrite unmanaged or edited {name}")

    backup_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = managed / "backups" / backup_id
    while backup.exists():
        backup_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = managed / "backups" / backup_id
    write_backup(backup, targets, saved_targets, saved_state)

    try:
        hashes = {}
        for name in TARGET_NAMES:
            target = targets[name]
            if name == "preview.yml" and preserve_preview:
                print("Existing preview.yml preserved; native lifecycle does not require replacing it")
                hashes[name] = digest(target.read_bytes())
                continue
            mode = 0o700 if name.endswith(".sh") else 0o600
            atomic_write(target, incoming[name], mode)
            if digest(target.read_bytes()) != digest(incoming[name]):
                raise RuntimeError(f"Verification failed: {name}")
            hashes[name] = digest(incoming[name])
        state = {"version": 2, "hashes": hashes, "backup": backup_id}
        atomic_write(state_path, (json.dumps(state, indent=2) + "\n").encode("utf-8"), 0o600)
    except Exception as error:
        try:
            for name in TARGET_NAMES:
                restore_snapshot(targets[name], saved_targets[name])
            restore_snapshot(state_path, saved_state)
        except Exception as rollback_error:
            raise RuntimeError(f"Installation failed and rollback failed: {error}; {rollback_error}") from error
        raise RuntimeError(f"Installation failed; changes rolled back: {error}") from error
    print("KEEPALIVE_INSTALL_OK")


def rollback(root, supervisor_root="/usr/local/share/supervisor"):
    root = Path(root).absolute()
    managed = root / ".keepalive"
    supervisor_dir = Path(supervisor_root).absolute()
    validate_directories(root, managed, root / ".vscode", supervisor_dir)
    state_path = managed / "install-state.json"
    state = read_state(state_path)
    if state.get("version") != 2 or not state.get("backup"):
        raise RuntimeError("No version 2 installation is available for rollback")
    targets = target_paths(root, supervisor_dir)
    hashes = state.get("hashes", {})
    for name in TARGET_NAMES:
        target = targets[name]
        if target.is_symlink() or not target.is_file() or digest(target.read_bytes()) != hashes.get(name):
            raise RuntimeError(f"Refusing rollback after external change: {name}")
    backup = managed / "backups" / state["backup"]
    manifest_path = backup / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("Installation backup manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1:
        raise RuntimeError("Unsupported installation backup")
    saved_targets = {}
    for name in TARGET_NAMES:
        metadata = manifest.get("targets", {}).get(name)
        if not isinstance(metadata, dict):
            raise RuntimeError(f"Installation backup is incomplete: {name}")
        path = backup / name
        saved_targets[name] = {
            "exists": bool(metadata.get("exists")),
            "mode": metadata.get("mode"),
            "content": path.read_bytes() if metadata.get("exists") else b"",
        }
    state_metadata = manifest.get("state", {})
    saved_state = {
        "exists": bool(state_metadata.get("exists")),
        "mode": state_metadata.get("mode"),
        "content": (backup / "install-state.json").read_bytes() if state_metadata.get("exists") else b"",
    }
    for name in TARGET_NAMES:
        restore_snapshot(targets[name], saved_targets[name])
    restore_snapshot(state_path, saved_state)
    print("KEEPALIVE_ROLLBACK_OK")


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise RuntimeError("Expected an installer bundle argument or rollback")
        if sys.argv[1] == "rollback":
            rollback("/workspace")
        else:
            install("/workspace", json.loads(base64.b64decode(sys.argv[1], validate=True)))
    except Exception as error:
        print(f"Startup installation failed: {error}", file=sys.stderr)
        sys.exit(1)
