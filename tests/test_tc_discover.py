import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "tc_discover", REPO / ".github" / "scripts" / "tc-discover.py"
)
discover = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(discover)

ROWS = [
    {"SpaceKey": "parked", "Name": "first-display-name", "Status": "STOPPED"},
    {"SpaceKey": "active", "Name": "second-display-name", "Status": "RUNNING"},
    {"SpaceKey": "removed", "Status": "INVALID"},
]


class WorkspaceSelectionTests(unittest.TestCase):
    def select(self, raw, rows=ROWS):
        return discover.select_space_keys(rows, discover.parse_space_keys(raw))

    def test_only_explicit_workspace_is_selected(self):
        self.assertEqual(self.select("active"), ["active"])

    def test_selection_order_not_api_order_and_duplicates_removed(self):
        self.assertEqual(self.select(" active,parked,active, parked "), ["active", "parked"])

    def test_stopped_workspace_requires_explicit_selection(self):
        self.assertEqual(self.select("parked"), ["parked"])

    def test_missing_or_blank_allowlist_fails_closed(self):
        for value in (None, "", " ", "\t", "\r\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                discover.parse_space_keys(value)

    def test_invalid_or_output_injecting_allowlist_rejected(self):
        for value in (
            "*", ",active", "active,", "active,,parked", "active;parked",
            "active parked", "active\nfirst=parked", "active\n", "active\r",
            'active",INJECTED="true', "active\\parked", "工作区", "../active",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                discover.parse_space_keys(value)

    def test_safe_key_characters_and_surrounding_horizontal_whitespace(self):
        self.assertEqual(discover.parse_space_keys(" space-key_1,\tKey2 "), ["space-key_1", "Key2"])

    def test_unknown_key_fails_entire_selection(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            self.select("active,missing")

    def test_recycled_key_fails_entire_selection(self):
        with self.assertRaisesRegex(ValueError, "removed"):
            self.select("active,removed")

    def test_display_name_does_not_override_real_key(self):
        with self.assertRaises(ValueError):
            self.select("second-display-name")

    def test_legacy_name_only_response(self):
        self.assertEqual(self.select("legacy", [{"Name": "legacy", "Status": "RUNNING"}]), ["legacy"])

    def test_empty_or_missing_keys_cannot_expand_selection(self):
        for rows in ([], [{}], [{"Status": "RUNNING"}], [{"SpaceKey": None}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.select("active", rows)

    def test_malformed_workspace_list_fails_closed(self):
        for rows in (None, {}, "active", [None], ["active"]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.select("active", rows)


class DiscoveryOutputTests(unittest.TestCase):
    def run_discovery(self, selection, response=None):
        body = response if response is not None else {"Response": {"Data": ROWS}}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output"
            env = {
                "TENCENT_SECRET_ID": "test-id", "TENCENT_SECRET_KEY": "test-key",
                "GITHUB_OUTPUT": str(output),
            }
            if selection is not None:
                env["SPACE_KEYS"] = selection
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(discover.urllib.request, "urlopen", return_value=io.StringIO(json.dumps(body))) as network, \
                    contextlib.redirect_stdout(io.StringIO()):
                result = discover.main()
            content = output.read_bytes() if output.exists() else b""
            return result, content, network

    def test_outputs_and_smoke_target_only_include_allowlist(self):
        result, content, network = self.run_discovery("active")
        self.assertEqual(result, 0)
        self.assertEqual(content, b"space_keys=active\nfirst=active\n")
        network.assert_called_once()
        request = network.call_args.args[0]
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers["x-tc-action"], "DescribeWorkspaces")
        self.assertEqual(request.data, b"{}")

    def test_first_is_first_selected_not_first_discovered(self):
        result, content, _ = self.run_discovery("active,parked,active")
        self.assertEqual(result, 0)
        self.assertEqual(content, b"space_keys=active,parked\nfirst=active\n")

    def test_invalid_config_never_calls_cloud_api_or_writes_output(self):
        for selection in (None, "", " ", "active,,parked", "active\nfirst=parked"):
            with self.subTest(selection=selection):
                result, content, network = self.run_discovery(selection)
                self.assertEqual(result, 1)
                self.assertEqual(content, b"")
                network.assert_not_called()

    def test_unknown_and_recycled_keys_produce_no_partial_output(self):
        for selection in ("missing", "active,missing", "active,removed"):
            with self.subTest(selection=selection):
                result, content, network = self.run_discovery(selection)
                self.assertEqual(result, 1)
                self.assertEqual(content, b"")
                network.assert_called_once()

    def test_supported_workspace_list_response_shapes(self):
        for response in (
            {"Response": {"Data": ROWS}},
            {"Response": {"Data": {"WorkspaceList": ROWS}}},
            {"Response": {"WorkspaceList": ROWS}},
        ):
            with self.subTest(response=response):
                result, content, _ = self.run_discovery("active", response)
                self.assertEqual(result, 0)
                self.assertEqual(content, b"space_keys=active\nfirst=active\n")

    def test_empty_account_does_not_write_output(self):
        result, content, _ = self.run_discovery("active", {"Response": {"Data": []}})
        self.assertEqual(result, 1)
        self.assertEqual(content, b"")

    def test_setup_and_deploy_both_pass_repository_allowlist(self):
        for name in ("setup.yml", "deploy.yml"):
            with self.subTest(workflow=name):
                workflow = (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")
                step = workflow.split("id: discover\n", 1)[1].split("run: python3 .github/scripts/tc-discover.py", 1)[0]
                self.assertIn("SPACE_KEYS: ${{ vars.SPACE_KEYS }}", step)

    def test_selection_script_changes_trigger_main_deployment(self):
        workflow = (REPO / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
        paths = workflow.split("    paths:\n", 1)[1].split("\nconcurrency:", 1)[0]
        self.assertIn('".github/scripts/tc-discover.py"', paths)


if __name__ == "__main__":
    unittest.main()
