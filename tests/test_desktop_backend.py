import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("desktop_backend", Path(__file__).resolve().parents[1] / "desktop-codex-backend/claude-backend.py")
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class Profiles(unittest.TestCase):
    def test_restore_original_and_keep_new_unrelated_settings(self):
        initial = {"model": "opus", "hooks": {"keep": True}, "env": {"ANTHROPIC_AUTH_TOKEN": "old", "OTHER": "1"}}
        active, state = backend.activate(initial, {}, backend.codex_env("local"))
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
