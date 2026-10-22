import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_state", ROOT / "scripts/build-state.py")
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)
FINGERPRINT = "a" * 64
DIGEST = "sha256:" + "b" * 64


class BuildStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.architectures = ["amd64", "arm64"]
        for architecture in self.architectures:
            directory = self.root / architecture
            (directory / "sources").mkdir(parents=True)
            (directory / "status").write_text(
                "Package: bash\nStatus: install ok installed\nArchitecture: " + architecture
                + "\nVersion: 1.0\nDescription: fixture\n\n"
            )
            (directory / "sources/ubuntu.list").write_text("deb https://archive.ubuntu.com/ubuntu noble main\n")

    def published(self, architectures=None):
        state.mark_published(self.root, FINGERPRINT, architectures or self.architectures, DIGEST)

    def check(self, fingerprint=FINGERPRINT, event="schedule"):
        return state.needs_build(self.root, fingerprint, self.architectures, event)[0]

    def test_first_run_builds_without_a_cached_publication(self):
        with patch.object(state, "package_updates") as updates:
            self.assertTrue(self.check())
            updates.assert_not_called()

    def test_a_snapshot_can_select_arm64_without_amd64(self):
        self.assertEqual(state.platforms("linux/arm64"), ["arm64"])

    def test_unchanged_published_runtime_skips_only_after_checking_both_architectures(self):
        self.published()
        with patch.object(state, "package_updates", return_value=False) as updates:
            self.assertFalse(self.check())
            self.assertEqual([call.args[1] for call in updates.call_args_list], self.architectures)

    def test_changed_base_or_sources_trigger_a_build(self):
        self.published()
        with patch.object(state, "package_updates") as updates:
            self.assertTrue(self.check(fingerprint="c" * 64))
            updates.assert_not_called()

    def test_an_update_only_on_arm64_still_triggers_a_build(self):
        self.published()
        with patch.object(state, "package_updates", side_effect=[False, True]):
            self.assertTrue(self.check())

    def test_package_network_errors_fail_instead_of_skipping(self):
        self.published()
        with patch.object(state, "package_updates", side_effect=RuntimeError("repository unavailable")):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                self.check()

    def test_manual_push_and_pull_request_runs_always_build(self):
        self.published()
        for event in ["workflow_dispatch", "push", "pull_request"]:
            with self.subTest(event=event), patch.object(state, "package_updates") as updates:
                self.assertTrue(self.check(event=event))
                updates.assert_not_called()

    def test_missing_or_empty_snapshots_do_not_skip(self):
        self.published()
        for file in ["arm64/status", "arm64/sources/ubuntu.list"]:
            with self.subTest(file=file), patch.object(state, "package_updates", return_value=False):
                path = self.root / file
                original = path.read_text()
                path.write_text("")
                self.assertTrue(self.check())
                path.write_text(original)

    def test_an_additional_architecture_requires_a_new_build(self):
        self.published(["amd64"])
        self.assertTrue(self.check())

    def test_incomplete_state_cannot_be_marked_as_published(self):
        (self.root / "arm64/status").unlink()
        with self.assertRaises(ValueError):
            self.published()
        self.assertFalse((self.root / "published.json").exists())

    def test_invalid_manifest_digest_cannot_be_marked_as_published(self):
        with self.assertRaises(ValueError):
            state.mark_published(self.root, FINGERPRINT, self.architectures, "latest")

    def test_apt_uses_isolated_configuration_and_a_strict_update_before_simulation(self):
        calls = []

        def apt(command, **kwargs):
            config = Path(kwargs["env"]["APT_CONFIG"]).read_text()
            self.assertIn('Dir::Etc::main "/dev/null";', config)
            self.assertIn('Dir::State::status ' + json.dumps(str((self.root / "arm64/status").resolve())) + ';', config)
            self.assertIn('APT::Architectures { "arm64"; };', config)
            self.assertIn('Dir::Etc::preferences "/dev/null";', config)
            calls.append(command)
            return "Inst bash [1.0] (1.1 Ubuntu:24.04/noble-updates [arm64])\n" if "--simulate" in command else ""

        with patch.object(state, "run", side_effect=apt):
            self.assertTrue(state.package_updates(self.root / "arm64", "arm64"))
        self.assertEqual(calls, [["apt-get", "update", "--error-on=any"], ["apt-get", "--simulate", "upgrade"]])

    def test_unrelated_repository_updates_do_not_trigger_a_build(self):
        with patch.object(state, "run", side_effect=["Fetched new repository indexes", "0 upgraded, 0 newly installed\n"]):
            self.assertFalse(state.package_updates(self.root / "amd64", "amd64"))

    def test_cli_outputs_an_explicit_build_decision_without_creating_a_publication(self):
        output = self.root / "github-output"
        result = subprocess.run([
            os.sys.executable, str(ROOT / "scripts/build-state.py"), "check", "--state", str(self.root),
            "--fingerprint", FINGERPRINT, "--event", "schedule", "--github-output", str(output),
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_text(), "build=true\n")
        self.assertFalse((self.root / "published.json").exists())


if __name__ == "__main__":
    unittest.main()
