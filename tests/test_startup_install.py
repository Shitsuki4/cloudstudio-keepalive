import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
INSTALLER_PATH = REPO / ".github" / "scripts" / "install-startup.py"
SPEC = importlib.util.spec_from_file_location("install_startup", INSTALLER_PATH)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def bundle():
    startup = REPO / "startup"
    return {
        name: base64.b64encode((startup / name).read_bytes()).decode("ascii")
        for name in ("boot.sh", "supervisor-start.sh", "preview.yml", "keepalive-boot.conf")
    }


class StartupInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "workspace"
        self.root.mkdir()
        self.supervisor = Path(self.temp.name) / "supervisor"
        self.supervisor.mkdir()
        self.bundle = bundle()

    def tearDown(self):
        self.temp.cleanup()

    def install(self):
        installer.install(self.root, self.bundle, self.supervisor)

    def test_installs_and_records_all_managed_files(self):
        self.install()
        state = json.loads((self.root / ".keepalive" / "install-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["version"], 2)
        self.assertEqual(set(state["hashes"]), {
            "boot.sh",
            "supervisor-start.sh",
            "preview.yml",
            "keepalive-boot.conf",
            "supervisor-include.conf",
        })
        self.assertTrue((self.root / ".keepalive" / "boot.sh").is_file())
        self.assertTrue((self.root / ".keepalive" / "supervisor-start.sh").is_file())
        self.assertTrue((self.root / ".keepalive" / "keepalive-boot.conf").is_file())
        self.assertTrue((self.root / ".vscode" / "preview.yml").is_file())
        config = self.supervisor / "keepalive-boot.conf"
        self.assertTrue(config.is_file())
        self.assertEqual(config.read_bytes(), (self.root / ".keepalive" / "keepalive-boot.conf").read_bytes())

    def test_repeat_install_is_idempotent(self):
        self.install()
        self.install()
        self.assertTrue((self.supervisor / "keepalive-boot.conf").is_file())

    def test_refuses_modified_managed_supervisor_config(self):
        self.install()
        config = self.supervisor / "keepalive-boot.conf"
        config.write_text("[program:external]\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "edited supervisor-include.conf"):
            self.install()
        self.assertEqual(config.read_text(encoding="utf-8"), "[program:external]\n")

    def test_preserves_existing_preview_file(self):
        preview = self.root / ".vscode"
        preview.mkdir()
        target = preview / "preview.yml"
        target.write_text("apps: []\n", encoding="utf-8")
        self.install()
        self.assertEqual(target.read_text(encoding="utf-8"), "apps: []\n")
        state = json.loads((self.root / ".keepalive" / "install-state.json").read_text(encoding="utf-8"))
        self.assertIn("preview.yml", state["hashes"])
        self.install()
        self.assertEqual(target.read_text(encoding="utf-8"), "apps: []\n")

    def test_rolls_back_when_a_write_fails(self):
        self.install()
        managed = self.root / ".keepalive"
        before = {name: (managed / name).read_bytes() for name in ("boot.sh", "supervisor-start.sh")}
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
        self.assertEqual((managed / "boot.sh").read_bytes(), before["boot.sh"])
        self.assertEqual((managed / "supervisor-start.sh").read_bytes(), before["supervisor-start.sh"])
        self.assertEqual((managed / "install-state.json").read_bytes(), state_before)

    def test_rollback_restores_files_and_refuses_external_change(self):
        self.install()
        managed = self.root / ".keepalive"
        (managed / "boot.sh").write_bytes((managed / "boot.sh").read_bytes() + b"\n")
        with self.assertRaisesRegex(RuntimeError, "external change"):
            installer.rollback(self.root, self.supervisor)
        self.assertTrue((self.supervisor / "keepalive-boot.conf").is_file())

    def test_rollback_removes_new_managed_files(self):
        self.install()
        installer.rollback(self.root, self.supervisor)
        self.assertFalse((self.root / ".keepalive" / "boot.sh").exists())
        self.assertFalse((self.root / ".keepalive" / "supervisor-start.sh").exists())
        self.assertFalse((self.supervisor / "keepalive-boot.conf").exists())
        self.assertFalse((self.root / ".keepalive" / "install-state.json").exists())


if __name__ == "__main__":
    unittest.main()
