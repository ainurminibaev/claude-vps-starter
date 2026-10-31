import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "configure_effort", Path(__file__).resolve().parents[1] / "codex-backend/configure_effort.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class EffortPolicy(unittest.TestCase):
    def test_migration_preserves_custom_rules_and_reverts(self):
        legacy = {"models": [{"name": "gpt-6.1-sol", "protocol": "codex"}],
                  "params": {"reasoning.effort": "high"}}
        custom = {"models": [{"name": "other"}], "params": {"custom": "keep"}}
        original = {"access": {"api-keys": ["private-sentinel"]},
                    "requests": {"payload": {"override": [legacy, custom, module.backend.auto_mode_override()]}}}
        updated = module.configure(copy.deepcopy(original))
        self.assertEqual(updated["access"], original["access"])
        self.assertEqual(updated["requests"]["payload"]["override"],
                         [module.backend.auxiliary_effort_override(), module.backend.classifier_effort_override(), custom, module.backend.auto_mode_override()])
        self.assertEqual(module.configure(copy.deepcopy(updated)), updated)
        self.assertEqual(module.configure(copy.deepcopy(updated), revert=True), original)

    def test_custom_defaults_and_partial_legacy_rules_are_preserved(self):
        custom = {"models": [{"name": "gpt-6.1-sol", "protocol": "codex"}],
                  "params": {"reasoning.effort": "high", "custom": "keep"}}
        data = {"requests": {"payload": {"default": [custom], "override": [custom]}}}
        updated = module.configure(data)
        self.assertEqual(updated["requests"]["payload"]["default"][0], custom)
        self.assertIn(custom, updated["requests"]["payload"]["override"])
