import importlib.util
from pathlib import Path
import unittest
import tempfile

spec = importlib.util.spec_from_file_location("desktop_backend", Path(__file__).resolve().parents[1] / "desktop-codex-backend/claude-backend.py")
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class Profiles(unittest.TestCase):
    def test_consent_workflow_is_profile_scoped_and_preserves_other_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = backend.Backend(directory)
            rules = Path(directory) / ".claude/rules"
            rules.mkdir(parents=True)
            unrelated = rules / "user.md"
            unrelated.write_text("Keep my instructions")
            instance.consent_workflow("codex")
            managed = rules / "codex-consent-workflow.md"
            self.assertTrue(managed.read_text().startswith("<!-- managed:"))
            instance.consent_workflow("codex")
            instance.consent_workflow("claude")
            self.assertFalse(managed.exists())
            self.assertEqual(unrelated.read_text(), "Keep my instructions")

    def test_consent_workflow_refuses_to_replace_user_file(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = backend.Backend(directory)
            path = Path(directory) / ".claude/rules/codex-consent-workflow.md"
            path.parent.mkdir(parents=True)
            path.write_text("My own rule")
            for profile in ("codex", "claude"):
                with self.assertRaises(ValueError):
                    instance.consent_workflow(profile)
            self.assertEqual(path.read_text(), "My own rule")

    def test_selected_defaults_restore_and_reapply_without_effort_lock(self):
        initial = {"model": "opus", "hooks": {"keep": True}}
        selected = {"model": "gpt-6-astra", "effortLevel": "medium"}
        active, state = backend.activate(initial, {}, backend.codex_env("key"), selected)
        self.assertEqual(active["model"], "gpt-6-astra")
        self.assertEqual(active["effortLevel"], "medium")
        self.assertNotIn("CLAUDE_CODE_EFFORT_LEVEL", active["env"])
        active, state = backend.activate(active, state, backend.codex_env("key"), {"effortLevel": "high"})
        native, state = backend.deactivate(active, state)
        self.assertEqual(native, initial)
        native["model"] = "sonnet"
        again, state = backend.activate(native, state, backend.codex_env("key"))
        self.assertEqual(again["model"], "gpt-6-astra")
        self.assertEqual(again["effortLevel"], "high")
        self.assertEqual(backend.deactivate(again, state)[0], native)

    def test_classifier_alias_is_present(self):
        self.assertIn("claude-sonnet-5", backend.ALIASES)

    def test_restore_original_and_keep_new_unrelated_settings(self):
        initial = {"model": "opus", "hooks": {"keep": True}, "env": {"ANTHROPIC_AUTH_TOKEN": "old", "OTHER": "1", "CLAUDE_CODE_AUTO_MODE_SERVER": "1", "CLAUDE_CODE_EFFORT_LEVEL": "high"}}
        active, state = backend.activate(initial, {}, backend.codex_env("local"))
        self.assertEqual(active["env"]["CLAUDE_CODE_AUTO_MODE_SERVER"], "0")
        self.assertNotIn("CLAUDE_CODE_EFFORT_LEVEL", active["env"])
        active["env"]["NEW"] = "2"
        active["language"] = "Russian"
        restored, state = backend.deactivate(active, state)
        self.assertEqual(restored, {**initial, "language": "Russian", "env": {**initial["env"], "NEW": "2"}})
        self.assertEqual(state["active"], "claude")
        self.assertEqual(initial["env"]["ANTHROPIC_AUTH_TOKEN"], "old")

    def test_roundtrip_absent_and_empty_environment(self):
        for initial in ({"model": "opus"}, {"env": {}}):
            active, state = backend.activate(initial, {}, backend.codex_env("key"))
            active, state = backend.activate(active, state, backend.codex_env("key2"))
            self.assertEqual(backend.deactivate(active, state)[0], initial)

    def test_new_native_environment_is_captured_on_each_activation(self):
        active, state = backend.activate({}, {}, backend.codex_env("key"))
        native, state = backend.deactivate(active, state)
        native["env"] = {"ANTHROPIC_BASE_URL": "https://company-gateway.example"}
        active, state = backend.activate(native, state, backend.codex_env("key"))
        self.assertEqual(backend.deactivate(active, state)[0], native)


if __name__ == "__main__":
    unittest.main()
