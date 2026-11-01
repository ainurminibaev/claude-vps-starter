import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "channel_context", Path(__file__).resolve().parents[1] / "codex-backend/configure_channel_context.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ChannelContext(unittest.TestCase):
    def test_scoped_addition_preserves_permissions_defaults_and_custom_rules(self):
        original = {"permissions": {"deny": ["Read(//private/**)"]},
                    "autoMode": {"environment": ["$defaults", "Keep custom infra"],
                                 "allow": ["$defaults", "Keep custom exception"],
                                 "hard_deny": ["$defaults", "Keep custom denial"]}}
        access = {"allowFrom": ["123"], "groups": {"-456": {"allowFrom": ["789"]}}}
        updated = module.configure(original, access)
        self.assertEqual(updated["permissions"], original["permissions"])
        self.assertEqual(updated["autoMode"]["hard_deny"], original["autoMode"]["hard_deny"])
        env = updated["autoMode"]["environment"][-1]
        self.assertIn('"direct_senders": ["123"]', env)
        self.assertIn('"-456": ["789"]', env)
        self.assertEqual(module.configure(updated, access), updated)
        self.assertEqual(module.configure(updated, {}, remove=True), original)

    def test_empty_settings_roundtrip_keeps_default_rules(self):
        updated = module.configure({}, {"allowFrom": ["123"]})
        for key in ["environment", "allow"]:
            self.assertEqual(updated["autoMode"][key][0], "$defaults")
        self.assertEqual(module.configure(updated, {}, remove=True), {})

    def test_no_sender_scope_is_rejected(self):
        with self.assertRaises(ValueError):
            module.configure({}, {})

    def test_intentional_default_replacement_survives_roundtrip(self):
        original = {"autoMode": {"environment": [], "allow": []}}
        updated = module.configure(original, {"allowFrom": ["123"]})
        self.assertEqual(module.configure(updated, {}, remove=True), original)
        self.assertEqual(module.configure(original, {}, remove=True), original)
