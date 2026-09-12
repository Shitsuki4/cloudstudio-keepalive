from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[1]


class ShellSourceFormatTests(unittest.TestCase):
    def test_linux_shell_sources_have_lf_line_endings(self):
        scripts = [*REPO.glob("startup/*.sh"), *REPO.glob(".github/scripts/*.sh")]
        self.assertTrue(scripts)
        for script in scripts:
            with self.subTest(script=script.relative_to(REPO).as_posix()):
                self.assertNotIn(b"\r\n", script.read_bytes(), "CRLF breaks Linux shell execution")
