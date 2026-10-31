import copy
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "configure_auto_mode", Path(__file__).resolve().parents[1] / "codex-backend/configure_auto_mode.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AutoModeConfig(unittest.TestCase):
    def test_preserves_auth_routing_and_custom_payload_rules(self):
        original = {"access": {"api-keys": ["private-sentinel"]},
                    "routing": {"session-affinity": True},
                    "requests": {"payload": {"override": [
                        {"models": [{"name": "gpt-6.1-sol"}], "params": {"reasoning.effort": "high"}},
                        {"models": [{"name": "other"}], "params": {"custom": "keep"}},
                    ]}}}
        updated = module.configure(copy.deepcopy(original))
        self.assertEqual(updated["access"], original["access"])
        self.assertEqual(updated["routing"], original["routing"])
        self.assertEqual(updated["requests"]["payload"]["override"][:-1], original["requests"]["payload"]["override"])
        snapshot = copy.deepcopy(updated)
        self.assertEqual(module.configure(updated), snapshot)
        self.assertEqual(module.configure(updated, remove=True), original)


if __name__ == "__main__":
    unittest.main()
