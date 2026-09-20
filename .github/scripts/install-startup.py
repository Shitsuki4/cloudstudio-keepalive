import base64
import binascii
import configparser
import datetime
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import sys
import tempfile
import uuid


BUNDLE_NAMES = ("boot.sh", "supervisor-start.sh", "preview.yml", "keepalive-boot.conf")
TARGET_NAMES = ("boot.sh", "supervisor-start.sh", "preview.yml", "keepalive-boot.conf", "supervisor-include.conf")
STRICT_NAMES = ("boot.sh", "supervisor-start.sh", "keepalive-boot.conf", "supervisor-include.conf")
DEFAULT_SUPERVISOR_ROOT = "/usr/local/share/supervisor"
STATE_VERSION = 2
BACKUP_VERSION = 2
BACKUP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{7,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_MODES = {
    "boot.sh": 0o700,
    "supervisor-start.sh": 0o700,
    "preview.yml": 0o600,
    "keepalive-boot.conf": 0o600,
    "supervisor-include.conf": 0o600,
}


def digest(content):
    return hashlib.sha256(content).hexdigest()


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def _mode_matches(path, expected):
    """Windows chmod cannot enforce Unix mode bits; Linux remains strict."""
    return os.name == "nt" or _mode(path) == expected


def _is_present(path):
    return path.exists() or path.is_symlink()


def _require_regular(path, label):
    if path.is_symlink():
        raise RuntimeError(f"Refusing symlinked {label}")
    if not path.exists() or not path.is_file():
        raise RuntimeError(f"{label} is not a regular file")


def _require_real_directory(path, label):
    if path.is_symlink():
        raise RuntimeError(f"{label} is symlinked")
    if not path.exists() or not path.is_dir():
        raise RuntimeError(f"{label} is not a directory")


def _check_directory_access(path, label, write=False):
    required = os.R_OK | os.X_OK
    if write:
        required |= os.W_OK
    if not os.access(path, required):
        action = "write" if write else "read"
        raise RuntimeError(f"{label} is not accessible for {action}")
    mode = _mode(path)
    if write and not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeError(f"{label} has no write permission")


def _validate_existing_chain(path, label, leaf_directory=None):
    """Reject symlinked path components and require the existing path shape."""
    path = Path(path)
    components = list(path.parents)[::-1] + [path]
    for component in components:
        if component.is_symlink():
            raise RuntimeError(f"{label} contains a symlink: {component}")
        if not component.exists():
            raise RuntimeError(f"{label} is missing: {component}")
        if not component.is_dir():
            if component == path and leaf_directory is False:
                continue
            raise RuntimeError(f"{label} contains a non-directory: {component}")
        if not os.access(component, os.R_OK | os.X_OK):
            raise RuntimeError(f"{label} contains an inaccessible directory: {component}")
    if leaf_directory is True:
        _require_real_directory(path, label)


def _validate_optional_directory(path, label):
    if _is_present(path):
        _require_real_directory(path, label)
        _check_directory_access(path, label)


def atomic_write(path, content, mode):
    """Write and replace a file, flushing the file and parent directory where supported."""
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".keepalive-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_new_file(path, content, mode):
    """Create a file in a new backup directory without following a pre-existing link."""
    path = Path(path)
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, mode)
    except Exception:
        if path.exists() and not path.is_symlink():
            path.unlink()
        raise


def _validate_sha(value, label):
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise RuntimeError(f"Invalid SHA-256 for {label}")


def _validate_mode(value, label, allow_none=False):
    if allow_none and value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0o777:
        raise RuntimeError(f"Invalid mode for {label}")


def _decode_bundle(bundle):
    try:
        if not isinstance(bundle, dict):
            raise RuntimeError("bundle is not an object")
        if any(not isinstance(name, str) for name in bundle):
            raise RuntimeError("bundle keys must be strings")
        expected = set(BUNDLE_NAMES)
        actual = set(bundle)
        if actual != expected:
            details = []
            missing = expected - actual
            extra = actual - expected
            if missing:
                details.append("missing " + ", ".join(sorted(missing)))
            if extra:
                details.append("unexpected " + ", ".join(sorted(extra)))
            raise RuntimeError("; ".join(details))
        decoded = {}
        for name in BUNDLE_NAMES:
            if not isinstance(bundle[name], str):
                raise RuntimeError(f"{name} is not a string")
            decoded[name] = base64.b64decode(bundle[name], validate=True)
        return decoded
    except (RuntimeError, ValueError, binascii.Error) as error:
        raise RuntimeError(f"Invalid startup bundle: {error}") from error


def _validate_legacy_state_object(state):
    if not isinstance(state, dict) or set(state) not in (
        {"version", "hashes", "backup"},
        {"version", "hashes", "modes", "backup"},
    ):
        raise RuntimeError("Invalid legacy installation state schema")
    if state.get("version") != STATE_VERSION:
        raise RuntimeError("Unsupported installation state version")
    hashes = state.get("hashes")
    if not isinstance(hashes, dict) or set(hashes) != set(TARGET_NAMES):
        raise RuntimeError("Legacy installation state has incomplete hashes")
    for name in TARGET_NAMES:
        _validate_sha(hashes[name], name)
    modes = state.get("modes")
    if modes is not None:
        if not isinstance(modes, dict) or set(modes) != set(TARGET_NAMES):
            raise RuntimeError("Legacy installation state has incomplete modes")
        for name in TARGET_NAMES:
            _validate_mode(modes[name], name)
    backup = state.get("backup")
    if not isinstance(backup, str) or not BACKUP_ID_RE.fullmatch(backup):
        raise RuntimeError("Legacy installation state has an unsafe backup ID")
    return state


def _validate_state_object(state):
    if not isinstance(state, dict) or set(state) != {
        "version", "hashes", "modes", "backup", "backup_manifest_sha256"
    }:
        raise RuntimeError("Invalid installation state schema")
    if state.get("version") != STATE_VERSION:
        raise RuntimeError("Unsupported installation state version")
    hashes = state.get("hashes")
    modes = state.get("modes")
    if not isinstance(hashes, dict) or set(hashes) != set(TARGET_NAMES):
        raise RuntimeError("Installation state has incomplete hashes")
    if not isinstance(modes, dict) or set(modes) != set(TARGET_NAMES):
        raise RuntimeError("Installation state has incomplete modes")
    for name in TARGET_NAMES:
        _validate_sha(hashes[name], name)
        _validate_mode(modes[name], name)
    backup = state.get("backup")
    if not isinstance(backup, str) or not BACKUP_ID_RE.fullmatch(backup):
        raise RuntimeError("Installation state has an unsafe backup ID")
    _validate_sha(state["backup_manifest_sha256"], "backup manifest")
    return state


def read_state(path):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError("Refusing symlinked installation state")
    if not path.exists():
        return {}
    _require_regular(path, "installation state")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Invalid installation state: {error}") from error
    try:
        return _validate_state_object(state)
    except RuntimeError as error:
        try:
            _validate_legacy_state_object(state)
        except RuntimeError:
            raise error
        raise RuntimeError(
            "Legacy installation state is unauthenticated; use the rollback command saved "
            "from the original startup setup run, or manually verify and remove managed "
            "startup files before reinstalling"
        ) from error


def snapshot(path):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError(f"Refusing symlinked startup target: {path.name}")
    if path.exists() and not path.is_file():
        raise RuntimeError(f"Unsafe startup target: {path.name}")
    if not path.exists():
        return {"exists": False, "mode": None, "content": b""}
    return {"exists": True, "mode": _mode(path), "content": path.read_bytes()}


def _verify_snapshot(path, saved, label=None):
    label = label or Path(path).name
    current = snapshot(path)
    if current["exists"] != saved["exists"] or current["content"] != saved["content"]:
        raise RuntimeError(f"Verification failed after restore: {label}")
    if saved["exists"] and not _mode_matches(path, saved["mode"]):
        raise RuntimeError(f"Verification failed after restore: {label}")


def restore_snapshot(path, saved):
    path = Path(path)
    if saved["exists"]:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise RuntimeError(f"Cannot restore unsafe startup target: {path.name}")
        atomic_write(path, saved["content"], saved["mode"])
    else:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise RuntimeError(f"Cannot remove unsafe startup target: {path.name}")
        if path.exists():
            path.unlink()
    _verify_snapshot(path, saved)


def _backup_metadata(saved):
    return {
        "exists": bool(saved["exists"]),
        "mode": saved["mode"],
        "sha256": digest(saved["content"]) if saved["exists"] else None,
    }


def write_backup(backup_root, backup_id, saved_targets, saved_state):
    backup_root = Path(backup_root)
    _require_real_directory(backup_root, "backup directory")
    _check_directory_access(backup_root, "backup directory", write=True)
    final = backup_root / backup_id
    if final.exists() or final.is_symlink():
        raise RuntimeError(f"Backup already exists: {backup_id}")
    temporary = Path(tempfile.mkdtemp(prefix=".backup-", dir=str(backup_root)))
    os.chmod(temporary, 0o700)
    try:
        for name, saved in saved_targets.items():
            if saved["exists"]:
                _write_new_file(temporary / name, saved["content"], 0o600)
        if saved_state["exists"]:
            _write_new_file(temporary / "install-state.json", saved_state["content"], 0o600)
        manifest = {
            "version": BACKUP_VERSION,
            "targets": {name: _backup_metadata(saved_targets[name]) for name in TARGET_NAMES},
            "state": _backup_metadata(saved_state),
        }
        _write_new_file(
            temporary / "manifest.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            0o600,
        )
        os.replace(temporary, final)
        _validate_backup(final, backup_id)
        return final
    except Exception:
        if temporary.exists() and not temporary.is_symlink():
            shutil.rmtree(temporary)
        if final.exists() and not final.is_symlink() and final.is_dir():
            shutil.rmtree(final)
        raise


def target_paths(root, supervisor_dir):
    managed = Path(root) / ".keepalive"
    return {
        "boot.sh": managed / "boot.sh",
        "supervisor-start.sh": managed / "supervisor-start.sh",
        "preview.yml": Path(root) / ".vscode" / "preview.yml",
        "keepalive-boot.conf": managed / "keepalive-boot.conf",
        "supervisor-include.conf": Path(supervisor_dir) / "keepalive-boot.conf",
    }


def validate_directories(root, managed, preview_dir, supervisor_dir, create=False, created=None):
    """Validate paths; optionally create only the workspace-owned support directories."""
    root = Path(root)
    managed = Path(managed)
    preview_dir = Path(preview_dir)
    supervisor_dir = Path(supervisor_dir)
    _validate_existing_chain(root, "workspace", leaf_directory=True)
    _check_directory_access(root, "workspace")
    _validate_existing_chain(supervisor_dir, "Supervisor include path", leaf_directory=True)
    _check_directory_access(supervisor_dir, "Supervisor include directory", write=True)
    _validate_optional_directory(managed, "keepalive directory")
    _validate_optional_directory(preview_dir, "preview directory")
    if managed.exists():
        for child, label in (
            (managed / "backups", "backup directory"),
            (managed / "start.d", "start.d directory"),
            (managed / "logs", "logs directory"),
        ):
            _validate_optional_directory(child, label)
    if preview_dir.exists():
        _check_directory_access(preview_dir, "preview directory", write=True)
    created = created if created is not None else []
    if not create:
        return created

    def ensure(path, label):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise RuntimeError(f"Unsafe startup directory: {label}")
        if path.exists():
            _check_directory_access(path, label, write=True)
            return
        parent = path.parent
        ensure(parent, f"parent of {label}")
        path.mkdir(mode=0o700)
        created.append(path)
        _check_directory_access(path, label, write=True)

    ensure(managed, "keepalive directory")
    ensure(preview_dir, "preview directory")
    ensure(managed / "backups", "backup directory")
    ensure(managed / "start.d", "start.d directory")
    ensure(managed / "logs", "logs directory")
    return created


def _discover_supervisor_config():
    command_line = Path("/proc/1/cmdline")
    try:
        arguments = [part.decode("utf-8") for part in command_line.read_bytes().split(b"\0") if part]
    except (OSError, UnicodeDecodeError) as error:
        raise RuntimeError(
            "Unable to determine active supervisord configuration; confirm it manually before installing"
        ) from error
    for index, argument in enumerate(arguments):
        if argument in ("-c", "--configuration") and index + 1 < len(arguments):
            return Path(arguments[index + 1])
        if argument.startswith("--configuration="):
            return Path(argument.split("=", 1)[1])
        if argument.startswith("-c="):
            return Path(argument[3:])
    raise RuntimeError(
        "Unable to determine active supervisord configuration; confirm it manually before installing"
    )


def _include_patterns(config_path):
    _validate_existing_chain(config_path, "supervisord configuration", leaf_directory=False)
    _require_regular(config_path, "supervisord configuration")
    if not os.access(config_path, os.R_OK):
        raise RuntimeError("Supervisord configuration is not readable")
    parser = configparser.RawConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(config_path.read_text(encoding="utf-8"))
    except (OSError, configparser.Error, UnicodeError) as error:
        raise RuntimeError(f"Unable to parse active supervisord configuration: {error}") from error
    section = next((name for name in parser.sections() if name.lower() == "include"), None)
    if section is None:
        raise RuntimeError(
            "Active supervisord configuration has no [include] files rule; refusing to modify it"
        )
    value = next((value for key, value in parser.items(section, raw=True) if key.lower() == "files"), None)
    if not value:
        raise RuntimeError(
            "Active supervisord configuration has no [include] files rule; refusing to modify it"
        )
    try:
        patterns = shlex.split(value, comments=False, posix=True)
    except ValueError as error:
        raise RuntimeError(f"Invalid supervisord include rule: {error}") from error
    if not patterns:
        raise RuntimeError("Active supervisord [include] files rule is empty")
    return config_path, patterns


def _pattern_matches(pattern, config_path, target):
    pattern_path = Path(pattern)
    if not pattern_path.is_absolute():
        pattern_path = config_path.parent / pattern_path
    pattern_text = os.path.normcase(os.path.normpath(str(pattern_path))).replace("\\", "/")
    target_text = os.path.normcase(os.path.normpath(str(target))).replace("\\", "/")
    return fnmatch.fnmatchcase(target_text, pattern_text)


def validate_supervisor_include(supervisor_dir, target, supervisor_config=None):
    config_path = Path(supervisor_config) if supervisor_config else _discover_supervisor_config()
    _validate_existing_chain(config_path, "supervisord configuration", leaf_directory=False)
    _check_directory_access(config_path.parent, "supervisord configuration directory")
    _, patterns = _include_patterns(config_path)
    target = Path(target)
    if not any(_pattern_matches(pattern, config_path, target) for pattern in patterns):
        raise RuntimeError(
            f"Active supervisord configuration does not include {target}; refusing to install"
        )
    return config_path


def ensure_supervisor_directory(supervisor_dir, target, supervisor_config=None):
    """Create the include directory when supervisord already ships the include rule.

    Freshly built containers carry the [include] line in supervisord.conf but not the
    directory it points at. The include rule is what proves this is the right location,
    so validate it first and only then create the missing directory.
    """
    supervisor_dir = Path(supervisor_dir)
    validate_supervisor_include(supervisor_dir, target, supervisor_config)
    if supervisor_dir.is_symlink():
        raise RuntimeError("Supervisor include path is symlinked")
    if supervisor_dir.exists():
        if not supervisor_dir.is_dir():
            raise RuntimeError("Supervisor include path is not a directory")
        return
    _validate_existing_chain(supervisor_dir.parent, "Supervisor include parent", leaf_directory=True)
    _check_directory_access(supervisor_dir.parent, "Supervisor include parent", write=True)
    supervisor_dir.mkdir(mode=0o755)
    _check_directory_access(supervisor_dir, "Supervisor include path", write=True)


def _new_backup_id():
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}-{uuid.uuid4().hex[:12]}"


def _validate_transition(name, saved, incoming, previous, previous_hashes, previous_modes):
    if not saved["exists"]:
        if previous and name in STRICT_NAMES:
            raise RuntimeError(f"Refusing to recreate missing managed file: {name}")
        return False
    current_hash = digest(saved["content"])
    incoming_hash = digest(incoming[name])
    if name == "preview.yml":
        return current_hash != incoming_hash
    if not previous:
        if current_hash != incoming_hash:
            raise RuntimeError(f"Refusing to overwrite unmanaged or edited {name}")
        return False
    if previous_hashes.get(name) != current_hash:
        raise RuntimeError(f"Refusing to overwrite unmanaged or edited {name}")
    if saved["mode"] != previous_modes[name]:
        raise RuntimeError(f"Refusing to overwrite externally changed mode: {name}")
    return False


def _remove_backup(path):
    path = Path(path)
    if not _is_present(path):
        return
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"Refusing to remove unsafe backup: {path}")
    shutil.rmtree(path)


def _cleanup_directories(created):
    for directory in reversed(created):
        try:
            if directory.is_dir() and not directory.is_symlink():
                directory.rmdir()
        except OSError:
            pass


def _verify_installed(targets, state_path, state, incoming, preserve_preview):
    for name in TARGET_NAMES:
        target = targets[name]
        saved = snapshot(target)
        if not saved["exists"]:
            raise RuntimeError(f"Verification failed: {name} is missing")
        expected_content = saved["content"] if name == "preview.yml" and preserve_preview else incoming[name]
        if digest(expected_content) != state["hashes"][name] or digest(saved["content"]) != state["hashes"][name]:
            raise RuntimeError(f"Verification failed: {name}")
        if saved["mode"] != state["modes"][name]:
            raise RuntimeError(f"Verification failed: mode for {name}")
    _validate_state_object(state)
    state_snapshot = snapshot(state_path)
    if not state_snapshot["exists"] or not _mode_matches(state_path, 0o600):
        raise RuntimeError("Verification failed: install state")


def install(root, bundle, supervisor_root=DEFAULT_SUPERVISOR_ROOT, supervisor_config=None):
    root = Path(root).absolute()
    managed = root / ".keepalive"
    preview_dir = root / ".vscode"
    supervisor_dir = Path(supervisor_root).absolute()
    targets = target_paths(root, supervisor_dir)
    state_path = managed / "install-state.json"

    ensure_supervisor_directory(supervisor_dir, targets["supervisor-include.conf"], supervisor_config)
    validate_directories(root, managed, preview_dir, supervisor_dir, create=False)
    saved_state = snapshot(state_path)
    previous = read_state(state_path)
    previous_hashes = previous.get("hashes", {})
    previous_modes = previous.get("modes", {})
    decoded = _decode_bundle(bundle)
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
        if name == "preview.yml" and saved_targets[name]["exists"]:
            preserve_preview = _validate_transition(
                name, saved_targets[name], incoming, previous, previous_hashes, previous_modes
            ) or preserve_preview
        else:
            _validate_transition(name, saved_targets[name], incoming, previous, previous_hashes, previous_modes)

    created_directories = []
    backup = None
    transaction_started = False
    try:
        validate_directories(
            root,
            managed,
            preview_dir,
            supervisor_dir,
            create=True,
            created=created_directories,
        )
        backup_id = _new_backup_id()
        backup = write_backup(managed / "backups", backup_id, saved_targets, saved_state)
        transaction_started = True
        hashes = {}
        modes = {}
        for name in TARGET_NAMES:
            target = targets[name]
            if name == "preview.yml" and preserve_preview:
                print("Existing preview.yml preserved; native lifecycle does not require replacing it")
            else:
                atomic_write(target, incoming[name], EXPECTED_MODES[name])
            current = snapshot(target)
            if not current["exists"] or digest(current["content"]) != digest(incoming[name]) and name != "preview.yml":
                raise RuntimeError(f"Verification failed: {name}")
            if name != "preview.yml" and not _mode_matches(target, EXPECTED_MODES[name]):
                raise RuntimeError(f"Verification failed: mode for {name}")
            hashes[name] = digest(current["content"])
            modes[name] = current["mode"]
        state = {
            "version": STATE_VERSION,
            "hashes": hashes,
            "modes": modes,
            "backup": backup.name,
            "backup_manifest_sha256": digest((backup / "manifest.json").read_bytes()),
        }
        atomic_write(state_path, (json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8"), 0o600)
        _verify_installed(targets, state_path, state, incoming, preserve_preview)
    except Exception as error:
        recovery_error = None
        if transaction_started:
            try:
                for name in TARGET_NAMES:
                    restore_snapshot(targets[name], saved_targets[name])
                restore_snapshot(state_path, saved_state)
                for name in TARGET_NAMES:
                    _verify_snapshot(targets[name], saved_targets[name])
                _verify_snapshot(state_path, saved_state, "install-state.json")
            except Exception as rollback_error:
                recovery_error = rollback_error
        if backup is not None:
            try:
                _remove_backup(backup)
            except Exception as cleanup_error:
                recovery_error = recovery_error or cleanup_error
        _cleanup_directories(created_directories)
        if recovery_error is not None:
            raise RuntimeError(
                f"Installation failed and rollback failed: {error}; {recovery_error}"
            ) from error
        raise RuntimeError(f"Installation failed; changes rolled back: {error}") from error
    print(
        "KEEPALIVE_INSTALL_OK "
        f"version={STATE_VERSION} backup={backup.name} "
        + "hashes="
        + ",".join(f"{name}:{hashes[name][:12]}" for name in TARGET_NAMES)
    )


def _validate_backup(backup, backup_id, expected_manifest_sha=None):
    backup = Path(backup)
    if not BACKUP_ID_RE.fullmatch(backup_id):
        raise RuntimeError("Unsafe installation backup ID")
    if backup.name != backup_id or backup.is_symlink() or not backup.is_dir():
        raise RuntimeError("Installation backup directory is unsafe")
    if not _mode_matches(backup, 0o700):
        raise RuntimeError("Installation backup directory has an unsafe mode")
    children = list(backup.iterdir())
    allowed_names = set(TARGET_NAMES) | {"install-state.json", "manifest.json"}
    if any(child.name not in allowed_names for child in children):
        raise RuntimeError("Installation backup contains unexpected files")
    if any(child.is_symlink() or not child.is_file() for child in children):
        raise RuntimeError("Installation backup contains an unsafe entry")
    manifest_path = backup / "manifest.json"
    _require_regular(manifest_path, "installation backup manifest")
    if expected_manifest_sha is not None:
        _validate_sha(expected_manifest_sha, "backup manifest")
        if digest(manifest_path.read_bytes()) != expected_manifest_sha:
            raise RuntimeError("Installation backup manifest integrity check failed")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Invalid installation backup manifest: {error}") from error
    if not isinstance(manifest, dict) or set(manifest) != {"version", "targets", "state"}:
        raise RuntimeError("Invalid installation backup manifest schema")
    if manifest.get("version") != BACKUP_VERSION:
        raise RuntimeError("Unsupported installation backup")
    target_manifest = manifest.get("targets")
    if not isinstance(target_manifest, dict) or set(target_manifest) != set(TARGET_NAMES):
        raise RuntimeError("Installation backup has incomplete targets")
    state_manifest = manifest.get("state")
    if not isinstance(state_manifest, dict) or set(state_manifest) != {"exists", "mode", "sha256"}:
        raise RuntimeError("Installation backup has invalid state metadata")

    def load_saved(name, metadata, path):
        if not isinstance(metadata, dict) or set(metadata) != {"exists", "mode", "sha256"}:
            raise RuntimeError(f"Invalid installation backup metadata: {name}")
        exists = metadata["exists"]
        if not isinstance(exists, bool):
            raise RuntimeError(f"Invalid existence marker in backup: {name}")
        _validate_mode(metadata["mode"], name, allow_none=not exists)
        if exists:
            _validate_sha(metadata["sha256"], name)
            _require_regular(path, f"backup file {name}")
            content = path.read_bytes()
            if digest(content) != metadata["sha256"] or not _mode_matches(path, 0o600):
                raise RuntimeError(f"Installation backup integrity check failed: {name}")
        else:
            if _is_present(path):
                raise RuntimeError(f"Unexpected backup file for absent target: {name}")
            content = b""
            if metadata["sha256"] is not None:
                raise RuntimeError(f"Invalid absent-target hash in backup: {name}")
        return {"exists": exists, "mode": metadata["mode"], "content": content}

    saved_targets = {
        name: load_saved(name, target_manifest[name], backup / name)
        for name in TARGET_NAMES
    }
    saved_state = load_saved("install-state.json", manifest.get("state"), backup / "install-state.json")
    return saved_targets, saved_state


def _restore_transaction(targets, state_path, before_targets, before_state, saved_targets, saved_state):
    try:
        for name in TARGET_NAMES:
            restore_snapshot(targets[name], saved_targets[name])
        restore_snapshot(state_path, saved_state)
        for name in TARGET_NAMES:
            _verify_snapshot(targets[name], saved_targets[name])
        _verify_snapshot(state_path, saved_state, "install-state.json")
    except Exception as error:
        recovery_error = None
        try:
            for name in TARGET_NAMES:
                restore_snapshot(targets[name], before_targets[name])
            restore_snapshot(state_path, before_state)
        except Exception as rollback_error:
            recovery_error = rollback_error
        if recovery_error is not None:
            raise RuntimeError(f"Rollback failed and original files could not be restored: {error}; {recovery_error}") from error
        raise RuntimeError(f"Rollback failed; original files restored: {error}") from error


def rollback(root, supervisor_root=DEFAULT_SUPERVISOR_ROOT):
    root = Path(root).absolute()
    managed = root / ".keepalive"
    preview_dir = root / ".vscode"
    supervisor_dir = Path(supervisor_root).absolute()
    validate_directories(root, managed, preview_dir, supervisor_dir, create=False)
    state_path = managed / "install-state.json"
    state = read_state(state_path)
    if not state:
        raise RuntimeError("No version 2 installation is available for rollback")
    targets = target_paths(root, supervisor_dir)
    current_targets = {name: snapshot(targets[name]) for name in TARGET_NAMES}
    for name in TARGET_NAMES:
        current = current_targets[name]
        if (
            not current["exists"]
            or digest(current["content"]) != state["hashes"][name]
            or not _mode_matches(targets[name], state["modes"][name])
        ):
            raise RuntimeError(f"Refusing rollback after external change: {name}")
    backup_root = managed / "backups"
    _require_real_directory(backup_root, "backup directory")
    _validate_existing_chain(backup_root, "backup path", leaf_directory=True)
    backup_id = state["backup"]
    backup = backup_root / backup_id
    saved_targets, saved_state = _validate_backup(
        backup,
        backup_id,
        state["backup_manifest_sha256"],
    )
    current_state = snapshot(state_path)
    _restore_transaction(
        targets,
        state_path,
        current_targets,
        current_state,
        saved_targets,
        saved_state,
    )
    print(f"KEEPALIVE_ROLLBACK_OK backup={backup_id}")


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
