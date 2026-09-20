import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import stat
import subprocess
import tempfile
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
INSTALLER_PATH = REPO / ".github" / "scripts" / "install-startup.py"
PREPARE_PATH = REPO / ".github" / "scripts" / "prepare-startup.py"
BOOT_PATH = REPO / "startup" / "boot.sh"
SPEC = importlib.util.spec_from_file_location("install_startup", INSTALLER_PATH)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
PREPARE_SPEC = importlib.util.spec_from_file_location("prepare_startup", PREPARE_PATH)
prepare = importlib.util.module_from_spec(PREPARE_SPEC)
PREPARE_SPEC.loader.exec_module(prepare)


def bundle():
    startup = REPO / "startup"
    return {
        name: base64.b64encode((startup / name).read_bytes()).decode("ascii")
        for name in installer.BUNDLE_NAMES
    }


def supervisor_config(include_dir):
    return "[include]\nfiles = %s/*.conf\n" % Path(include_dir).as_posix()


class StartupInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "workspace"
        self.root.mkdir()
        self.supervisor = Path(self.temp.name) / "supervisor"
        self.supervisor.mkdir()
        self.config = Path(self.temp.name) / "supervisord.conf"
        self.config.write_text(supervisor_config(self.supervisor), encoding="utf-8")
        self.bundle = bundle()

    def tearDown(self):
        self.temp.cleanup()

    def install(self, bundle=None):
        installer.install(
            self.root,
            self.bundle if bundle is None else bundle,
            self.supervisor,
            supervisor_config=self.config,
        )

    def install_isolated(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / "workspace"
        root.mkdir()
        supervisor = Path(temp.name) / "supervisor"
        supervisor.mkdir()
        config = Path(temp.name) / "supervisord.conf"
        config.write_text(supervisor_config(supervisor), encoding="utf-8")
        installer.install(root, self.bundle, supervisor, supervisor_config=config)
        return root, supervisor, config

    def targets(self, root=None, supervisor=None):
        return installer.target_paths(self.root if root is None else root, self.supervisor if supervisor is None else supervisor)

    def latest_backup(self, root=None):
        root = self.root if root is None else Path(root)
        state_path = root / ".keepalive" / "install-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        return root / ".keepalive" / "backups" / state["backup"]

    def rewrite_backup_manifest(self, backup, transform, root=None):
        root = self.root if root is None else Path(root)
        state_path = root / ".keepalive" / "install-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        manifest_path = backup / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        transform(manifest)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        state["backup_manifest_sha256"] = installer.digest(manifest_path.read_bytes())
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def test_validate_supervisor_include_accepts_relative_pattern(self):
        config_dir = Path(self.temp.name) / "supervisor-config"
        include_dir = config_dir / "conf.d"
        include_dir.mkdir(parents=True)
        config = config_dir / "supervisord.conf"
        config.write_text("[include]\nfiles = conf.d/*.conf\n", encoding="utf-8")
        target = include_dir / "keepalive-boot.conf"
        self.assertEqual(
            installer.validate_supervisor_include(include_dir, target, config),
            config,
        )

    def test_validate_supervisor_include_accepts_multiple_quoted_patterns(self):
        target = self.supervisor / "keepalive-boot.conf"
        self.config.write_text(
            "[include]\nfiles = \"/tmp/not-matching/*.conf\" \"%s/*.conf\"\n" % self.supervisor.as_posix(),
            encoding="utf-8",
        )
        self.assertEqual(
            installer.validate_supervisor_include(self.supervisor, target, self.config),
            self.config,
        )

    def test_validate_supervisor_include_rejects_missing_or_nonmatching_rule(self):
        target = self.supervisor / "keepalive-boot.conf"
        self.config.write_text("[supervisord]\nnodaemon=true\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "no \[include\] files rule"):
            installer.validate_supervisor_include(self.supervisor, target, self.config)
        self.config.write_text("[include]\nfiles = /tmp/not-matching/*.conf\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "does not include"):
            installer.validate_supervisor_include(self.supervisor, target, self.config)

    def test_validate_supervisor_include_rejects_invalid_rule(self):
        target = self.supervisor / "keepalive-boot.conf"
        self.config.write_text("[include]\nfiles = \"unterminated\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Invalid supervisord include rule"):
            installer.validate_supervisor_include(self.supervisor, target, self.config)

    def test_creates_missing_supervisor_directory_when_include_rule_matches(self):
        shutil.rmtree(self.supervisor)
        self.install()
        self.assertTrue((self.supervisor / "keepalive-boot.conf").is_file())

    def test_refuses_missing_supervisor_directory_without_include_rule(self):
        shutil.rmtree(self.supervisor)
        self.config.write_text("[supervisord]\nnodaemon=true\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "no \[include\] files rule"):
            self.install()
        self.assertFalse(self.supervisor.exists())

    def test_refuses_missing_supervisor_directory_with_missing_parent(self):
        missing = Path(self.temp.name) / "absent" / "supervisor"
        self.config.write_text(
            "[include]\nfiles = %s/*.conf\n" % missing.as_posix(), encoding="utf-8"
        )
        with self.assertRaisesRegex(RuntimeError, "Supervisor include parent is missing"):
            installer.install(self.root, self.bundle, missing, supervisor_config=self.config)
        self.assertFalse(missing.exists())

    def test_installs_and_records_all_managed_files(self):
        self.install()
        state = json.loads((self.root / ".keepalive" / "install-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["version"], 2)
        self.assertEqual(set(state["hashes"]), set(installer.TARGET_NAMES))
        self.assertEqual(set(state["modes"]), set(installer.TARGET_NAMES))
        self.assertTrue((self.root / ".keepalive" / "boot.sh").is_file())
        self.assertTrue((self.root / ".keepalive" / "supervisor-start.sh").is_file())
        self.assertTrue((self.root / ".keepalive" / "keepalive-boot.conf").is_file())
        self.assertTrue((self.root / ".vscode" / "preview.yml").is_file())
        config = self.supervisor / "keepalive-boot.conf"
        self.assertTrue(config.is_file())
        self.assertEqual(config.read_bytes(), (self.root / ".keepalive" / "keepalive-boot.conf").read_bytes())
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)

    def test_repeat_install_is_idempotent_and_keeps_backup(self):
        self.install()
        backup_before = set((self.root / ".keepalive" / "backups").iterdir())
        self.install()
        backup_after = set((self.root / ".keepalive" / "backups").iterdir())
        self.assertGreaterEqual(len(backup_after), len(backup_before) + 1)

    def test_refuses_modified_managed_supervisor_config(self):
        self.install()
        config = self.supervisor / "keepalive-boot.conf"
        config.write_text("[program:external]\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "edited supervisor-include.conf"):
            self.install()
        self.assertEqual(config.read_text(encoding="utf-8"), "[program:external]\n")

    def test_rejects_invalid_bundle_without_creating_workspace_files(self):
        with self.assertRaisesRegex(RuntimeError, "Invalid startup bundle"):
            self.install({})
        self.assertFalse((self.root / ".keepalive").exists())

    def test_refuses_existing_strict_target_without_history(self):
        target = self.root / ".keepalive"
        target.mkdir()
        (target / "boot.sh").write_bytes(b"manual\n")
        with self.assertRaisesRegex(RuntimeError, "unmanaged"):
            self.install()
        self.assertEqual((target / "boot.sh").read_bytes(), b"manual\n")

    def test_accepts_identical_existing_strict_target_without_history(self):
        target = self.root / ".keepalive"
        target.mkdir()
        (target / "boot.sh").write_bytes(base64.b64decode(self.bundle["boot.sh"]))
        self.install()
        self.assertEqual((target / "boot.sh").read_bytes(), base64.b64decode(self.bundle["boot.sh"]))

    def test_refuses_modified_strict_targets_after_install(self):
        for name in installer.STRICT_NAMES:
            with self.subTest(name=name):
                root, supervisor, config = self.install_isolated()
                targets = self.targets(root, supervisor)
                original = targets[name].read_bytes()
                targets[name].write_bytes(original + b"\nexternal\n")
                with self.assertRaisesRegex(RuntimeError, "edited %s" % re.escape(name)):
                    installer.install(root, self.bundle, supervisor, supervisor_config=config)
                self.assertEqual(targets[name].read_bytes(), original + b"\nexternal\n")

    def test_refuses_missing_strict_targets_after_install(self):
        for name in installer.STRICT_NAMES:
            with self.subTest(name=name):
                root, supervisor, config = self.install_isolated()
                targets = self.targets(root, supervisor)
                targets[name].unlink()
                with self.assertRaisesRegex(RuntimeError, "recreate missing managed file: %s" % re.escape(name)):
                    installer.install(root, self.bundle, supervisor, supervisor_config=config)
                self.assertFalse(targets[name].exists())

    @unittest.skipIf(os.name == "nt", "Unix mode bits are not enforced on Windows")
    def test_refuses_changed_strict_target_modes_after_install(self):
        for name in installer.STRICT_NAMES:
            with self.subTest(name=name):
                root, supervisor, config = self.install_isolated()
                targets = self.targets(root, supervisor)
                os.chmod(targets[name], 0o644)
                with self.assertRaisesRegex(RuntimeError, "externally changed mode: %s" % re.escape(name)):
                    installer.install(root, self.bundle, supervisor, supervisor_config=config)

    def test_rejects_invalid_bundle_schema_without_creating_workspace_files(self):
        missing = dict(self.bundle)
        missing.pop("boot.sh")
        extra = dict(self.bundle)
        extra["extra.sh"] = self.bundle["boot.sh"]
        invalid_base64 = dict(self.bundle)
        invalid_base64["boot.sh"] = "not base64!"
        non_string = dict(self.bundle)
        non_string["boot.sh"] = 1
        non_string_key = {1: self.bundle["boot.sh"], **self.bundle}
        cases = [
            ("missing", missing),
            ("extra", extra),
            ("invalid-base64", invalid_base64),
            ("non-string-value", non_string),
            ("non-object", []),
            ("non-string-key", non_string_key),
        ]
        for label, case in cases:
            with self.subTest(label=label):
                root = Path(self.temp.name) / ("workspace-invalid-%s" % label)
                root.mkdir()
                with self.assertRaisesRegex(RuntimeError, "Invalid startup bundle"):
                    installer.install(root, case, self.supervisor, supervisor_config=self.config)
                self.assertFalse((root / ".keepalive").exists())

    def test_preserves_existing_preview_file(self):
        preview = self.root / ".vscode"
        preview.mkdir()
        target = preview / "preview.yml"
        target.write_text("apps: []\n", encoding="utf-8")
        self.install()
        self.assertEqual(target.read_text(encoding="utf-8"), "apps: []\n")
        self.install()
        self.assertEqual(target.read_text(encoding="utf-8"), "apps: []\n")

    def test_rejects_symlinked_state(self):
        managed = self.root / ".keepalive"
        managed.mkdir()
        state_target = Path(self.temp.name) / "state-target"
        state_target.write_text("{}\n", encoding="utf-8")
        (managed / "install-state.json").symlink_to(state_target)
        with self.assertRaisesRegex(RuntimeError, "symlinked"):
            self.install()

    def test_rolls_back_all_targets_when_a_write_fails(self):
        self.install()
        managed = self.root / ".keepalive"
        targets = installer.target_paths(self.root, self.supervisor)
        before = {name: installer.snapshot(targets[name]) for name in installer.TARGET_NAMES}
        state_before = (managed / "install-state.json").read_bytes()
        original = installer.atomic_write
        calls = 0

        def fail_on_second_write(path, content, mode):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated write failure")
            return original(path, content, mode)

        with mock.patch.object(installer, "atomic_write", side_effect=fail_on_second_write):
            with self.assertRaisesRegex(RuntimeError, "changes rolled back"):
                self.install()
        for name in installer.TARGET_NAMES:
            self.assertEqual(installer.snapshot(targets[name]), before[name])
        self.assertEqual((managed / "install-state.json").read_bytes(), state_before)

    def test_rollback_restores_files_and_refuses_external_change(self):
        self.install()
        managed = self.root / ".keepalive"
        (managed / "boot.sh").write_bytes((managed / "boot.sh").read_bytes() + b"\n")
        with self.assertRaisesRegex(RuntimeError, "external change"):
            installer.rollback(self.root, self.supervisor)
        self.assertTrue((self.supervisor / "keepalive-boot.conf").is_file())

    def test_rollback_removes_only_new_managed_files(self):
        start_script = self.root / ".keepalive" / "start.d" / "user.sh"
        log = self.root / ".keepalive" / "logs" / "user.log"
        self.install()
        start_script.write_text("#!/bin/sh\n", encoding="utf-8")
        log.write_text("keep\n", encoding="utf-8")
        installer.rollback(self.root, self.supervisor)
        self.assertFalse((self.root / ".keepalive" / "boot.sh").exists())
        self.assertFalse((self.root / ".keepalive" / "supervisor-start.sh").exists())
        self.assertFalse((self.supervisor / "keepalive-boot.conf").exists())
        self.assertFalse((self.root / ".keepalive" / "install-state.json").exists())
        self.assertTrue(start_script.exists())
        self.assertTrue(log.exists())

    def test_rejects_tampered_backup(self):
        self.install()
        self.install()
        state = json.loads((self.root / ".keepalive" / "install-state.json").read_text(encoding="utf-8"))
        backup = self.root / ".keepalive" / "backups" / state["backup"]
        (backup / "boot.sh").write_bytes(b"tampered\n")
        with self.assertRaisesRegex(RuntimeError, "integrity"):
            installer.rollback(self.root, self.supervisor)

    def test_rejects_tampered_backup_manifest(self):
        self.install()
        self.install()
        state_path = self.root / ".keepalive" / "install-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        manifest = self.root / ".keepalive" / "backups" / state["backup"] / "manifest.json"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace('"version": 2', '"version": 1'), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "manifest integrity"):
            installer.rollback(self.root, self.supervisor)

    def test_rejects_backup_with_unexpected_file(self):
        self.install()
        backup = self.latest_backup()
        (backup / "unknown.txt").write_text("unexpected\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "unexpected files"):
            installer.rollback(self.root, self.supervisor)

    def test_rejects_backup_file_for_absent_target(self):
        self.install()
        backup = self.latest_backup()
        (backup / "boot.sh").write_bytes(b"unexpected\n")
        with self.assertRaisesRegex(RuntimeError, "Unexpected backup file for absent target: boot.sh"):
            installer.rollback(self.root, self.supervisor)

    def test_rejects_backup_with_invalid_metadata(self):
        cases = [
            (
                "invalid-mode",
                True,
                lambda manifest: manifest["targets"]["boot.sh"].update(mode="600"),
                "Invalid mode",
            ),
            (
                "invalid-hash",
                True,
                lambda manifest: manifest["targets"]["boot.sh"].update(sha256="bad"),
                "Invalid SHA-256",
            ),
            (
                "absent-hash",
                False,
                lambda manifest: manifest["targets"]["boot.sh"].update(sha256="bad"),
                "Invalid absent-target hash",
            ),
        ]
        for label, repeat_install, transform, message in cases:
            with self.subTest(label=label):
                root, supervisor, config = self.install_isolated()
                if repeat_install:
                    installer.install(root, self.bundle, supervisor, supervisor_config=config)
                isolated_backup = self.latest_backup(root)
                self.rewrite_backup_manifest(isolated_backup, transform, root=root)
                with self.assertRaisesRegex(RuntimeError, message):
                    installer.rollback(root, supervisor)

    def test_rejects_backup_with_unsafe_entry(self):
        self.install()
        backup = self.latest_backup()
        target = backup / "boot.sh"
        if target.exists():
            target.unlink()
        target.mkdir()
        with self.assertRaisesRegex(RuntimeError, "unsafe entry"):
            installer.rollback(self.root, self.supervisor)

    @unittest.skipIf(os.name == "nt", "Symlink checks require Unix-like symlink behavior")
    def test_rejects_symlinked_backup_entry(self):
        self.install()
        backup = self.latest_backup()
        target = backup / "boot.sh"
        if target.exists():
            target.unlink()
        target.symlink_to(backup / "manifest.json")
        with self.assertRaisesRegex(RuntimeError, "unsafe entry"):
            installer.rollback(self.root, self.supervisor)

    def test_restore_transaction_reports_when_original_files_are_restored(self):
        self.install()
        targets = self.targets()
        state_path = self.root / ".keepalive" / "install-state.json"
        before_targets = {name: installer.snapshot(targets[name]) for name in installer.TARGET_NAMES}
        before_state = installer.snapshot(state_path)
        saved_targets = {
            name: {"exists": before_targets[name]["exists"], "mode": before_targets[name]["mode"], "content": b"rollback\n"}
            for name in installer.TARGET_NAMES
        }
        saved_state = {"exists": before_state["exists"], "mode": before_state["mode"], "content": b"rollback-state\n"}
        original_restore = installer.restore_snapshot
        calls = 0

        def fail_first_restore(path, saved):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("simulated backup restore failure")
            return original_restore(path, saved)

        with mock.patch.object(installer, "restore_snapshot", side_effect=fail_first_restore):
            with self.assertRaisesRegex(RuntimeError, "Rollback failed; original files restored"):
                installer._restore_transaction(targets, state_path, before_targets, before_state, saved_targets, saved_state)
        for name in installer.TARGET_NAMES:
            self.assertEqual(installer.snapshot(targets[name]), before_targets[name])
        self.assertEqual(installer.snapshot(state_path), before_state)

    def test_restore_transaction_reports_when_original_restore_fails(self):
        self.install()
        targets = self.targets()
        state_path = self.root / ".keepalive" / "install-state.json"
        before_targets = {name: installer.snapshot(targets[name]) for name in installer.TARGET_NAMES}
        before_state = installer.snapshot(state_path)
        saved_targets = {
            name: {"exists": before_targets[name]["exists"], "mode": before_targets[name]["mode"], "content": b"rollback\n"}
            for name in installer.TARGET_NAMES
        }
        saved_state = {"exists": before_state["exists"], "mode": before_state["mode"], "content": b"rollback-state\n"}

        with mock.patch.object(installer, "restore_snapshot", side_effect=OSError("simulated restore failure")):
            with self.assertRaisesRegex(RuntimeError, "Rollback failed and original files could not be restored"):
                installer._restore_transaction(targets, state_path, before_targets, before_state, saved_targets, saved_state)

    def test_refuses_legacy_state_without_authenticated_backup(self):
        self.install()
        state_path = self.root / ".keepalive" / "install-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        legacy_state = {
            "version": 2,
            "hashes": state["hashes"],
            "backup": state["backup"],
        }
        state_path.write_text(json.dumps(legacy_state, indent=2) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Legacy installation state is unauthenticated"):
            self.install()
        with self.assertRaisesRegex(RuntimeError, "Legacy installation state is unauthenticated"):
            installer.rollback(self.root, self.supervisor)


class PrepareStartupTests(unittest.TestCase):
    def test_commands_are_single_line_and_bundle_contains_sources(self):
        command = prepare.build_command()
        rollback = prepare.build_rollback_command()
        self.assertNotRegex(command + rollback, r"[\r\n]")
        parsed = shlex.split(command)
        self.assertEqual(parsed[0], "python3")
        payload = json.loads(base64.b64decode(parsed[-1], validate=True))
        self.assertEqual(set(payload), set(installer.BUNDLE_NAMES))
        self.assertIn("rollback", rollback)


def bash_available():
    executable = shutil.which("bash")
    if not executable:
        return False
    try:
        result = subprocess.run(
            [executable, "-c", "test -x /bin/bash && exec /bin/bash -c 'exit 0'"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@unittest.skipUnless(os.name != "nt" and bash_available(), "bash is required")
class BootScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "workspace"
        self.managed = self.root / ".keepalive"
        self.managed.mkdir(parents=True)
        self.supervisor = Path(self.temp.name) / "supervisor"
        self.supervisor.mkdir()
        persistent = REPO / "startup" / "keepalive-boot.conf"
        (self.managed / "keepalive-boot.conf").write_bytes(persistent.read_bytes())
        self.env = {
            **os.environ,
            "KEEPALIVE_SUPERVISOR_DIR": str(self.supervisor),
        }

    def tearDown(self):
        self.temp.cleanup()

    def run_boot(self, source):
        return subprocess.run(
            ["bash", str(self.managed / "boot.sh"), source],
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
        )

    def test_source_and_runtime_copy(self):
        shutil.copy2(BOOT_PATH, self.managed / "boot.sh")
        result = self.run_boot("supervisor")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.supervisor / "keepalive-boot.conf").read_bytes(),
            (self.managed / "keepalive-boot.conf").read_bytes(),
        )
        self.assertIn("source=supervisor", (self.managed / "logs" / "boot.log").read_text())

    def test_mismatch_fails_and_invalid_source_fails(self):
        shutil.copy2(BOOT_PATH, self.managed / "boot.sh")
        (self.supervisor / "keepalive-boot.conf").write_text("changed\n", encoding="utf-8")
        result = self.run_boot("lifecycle")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime_include_mismatch", (self.managed / "logs" / "boot.log").read_text())
        invalid = self.run_boot("bad")
        self.assertEqual(invalid.returncode, 2)

    def test_missing_supervisor_directory_is_created(self):
        shutil.copy2(BOOT_PATH, self.managed / "boot.sh")
        shutil.rmtree(self.supervisor)
        result = self.run_boot("lifecycle")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.supervisor / "keepalive-boot.conf").read_bytes(),
            (self.managed / "keepalive-boot.conf").read_bytes(),
        )

    def test_symlinked_supervisor_directory_is_rejected(self):
        shutil.copy2(BOOT_PATH, self.managed / "boot.sh")
        shutil.rmtree(self.supervisor)
        self.supervisor.symlink_to(self.root)
        result = self.run_boot("manual")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unavailable or symlinked", result.stderr)

    def test_records_container_resource_limits(self):
        shutil.copy2(BOOT_PATH, self.managed / "boot.sh")
        limits = Path(self.temp.name) / "cgroup"
        limits.mkdir()
        (limits / "cpu.max").write_text("100000 100000\n", encoding="utf-8")
        (limits / "memory.max").write_text("2147483648\n", encoding="utf-8")
        (limits / "memory.swap.max").write_text("0\n", encoding="utf-8")
        (limits / "memory.oom.group").write_text("1\n", encoding="utf-8")
        (limits / "memory.current").write_text("1634299904\n", encoding="utf-8")
        self.env["KEEPALIVE_CGROUP_DIR"] = str(limits)
        result = self.run_boot("manual")
        self.assertEqual(result.returncode, 0, result.stderr)
        log = (self.managed / "logs" / "boot.log").read_text()
        self.assertIn("cpu_max=100000/100000", log)
        self.assertIn("memory_max=2147483648", log)
        self.assertIn("memory_swap_max=0", log)
        self.assertIn("oom_group=1", log)
        self.assertIn("disk_used_total=", log)

    def test_unreadable_cgroup_directory_still_boots(self):
        shutil.copy2(BOOT_PATH, self.managed / "boot.sh")
        self.env["KEEPALIVE_CGROUP_DIR"] = str(Path(self.temp.name) / "absent")
        result = self.run_boot("manual")
        self.assertEqual(result.returncode, 0, result.stderr)
        log = (self.managed / "logs" / "boot.log").read_text()
        self.assertIn("event=limits cpu_max=unknown", log)


if __name__ == "__main__":
    unittest.main()
