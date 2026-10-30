"""Ensure only the gateway's known informational modal is acknowledged."""
import os
from pathlib import Path
import re
import subprocess
import unittest


WATCHDOG = Path(__file__).resolve().parents[1] / "scripts/claude-tg-watchdog.sh"
FUNCTION = re.search(
    r"^is_classifier_billing_notice\(\) \{\n.*?^\}",
    WATCHDOG.read_text(), re.MULTILINE | re.DOTALL,
).group(0)
NOTICE = """We're changing auto mode to no longer charge for classifier requests in Claude Code.
However, this session isn't eligible because your requests go through 127.0.0.1:18418.
Nothing breaks: auto mode keeps working, and its classifier requests are billed as before.
https://code.claude.com/docs/en/auto-mode-classifier-billing

  Enter to continue · Esc to cancel
"""


class WatchdogDialogTests(unittest.TestCase):
    def matches(self, pane):
        env = {**os.environ, "DIALOG_PANE": pane}
        result = subprocess.run(
            ["bash", "-c", FUNCTION + '\nis_classifier_billing_notice "$DIALOG_PANE"'],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(result.stderr, "")
        self.assertIn(result.returncode, [0, 1])
        return result.returncode == 0

    def test_active_billing_modal_is_recognized(self):
        self.assertTrue(self.matches(NOTICE))

    def test_generic_permission_prompt_is_not_acknowledged(self):
        self.assertFalse(self.matches("Allow this command?\nEnter to continue · Esc to cancel\n"))

    def test_quoted_notice_with_normal_prompt_is_not_acknowledged(self):
        self.assertFalse(self.matches(NOTICE + "\n❯\n  auto mode on\n"))

    def test_old_notice_in_scrollback_is_not_acknowledged(self):
        self.assertFalse(self.matches(NOTICE + "\n" * 30 + "Enter to continue · Esc to cancel\n"))

    def test_notice_without_active_footer_is_not_acknowledged(self):
        self.assertFalse(self.matches(NOTICE.replace("Enter to continue · Esc to cancel", "")))


if __name__ == "__main__":
    unittest.main()
