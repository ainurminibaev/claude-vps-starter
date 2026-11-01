"""Exercise the real profile selector and policy rollback without touching a bot."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]


class BackendSwitch(unittest.TestCase):
    def test_codex_policy_is_removed_when_returning_to_native_claude(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            channel = home / ".claude/channels/telegram"
            channel.mkdir(parents=True)
            (channel / "access.json").write_text(json.dumps({"allowFrom": ["123"]}))
            config = home / ".config/codex-model-proxy"
            config.mkdir(parents=True)
            (config / "client.env").write_text("CODEX_PROXY_KEY=test\n")
            settings = home / ".claude/settings.json"
            original = {"permissions": {"deny": ["Read(//private/**)"]}, "model": "opus"}
            settings.write_text(json.dumps(original))
            env = {**os.environ, "BOT_TEST_HOME": str(home),
                   "CLAUDE_BOT_CONTEXT_CONFIG_FILE": str(REPO / "codex-backend/configure_channel_context.py")}
            shell = '''getent() { printf 'test-user:x:0:0::%s:/bin/bash\\n' "$BOT_TEST_HOME"; }
id() { printf 'test-group\\n'; }
chown() { :; }
systemctl() { return 0; }
export -f getent id chown systemctl
exec bash "$1" test-user "$2"
'''
            for profile in ["codex", "claude"]:
                run = subprocess.run(["bash", "-c", shell, "test", str(REPO / "scripts/bot-backend.sh"), profile],
                                     env=env, capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                data = json.loads(settings.read_text())
                self.assertEqual(data["permissions"], original["permissions"])
                if profile == "codex":
                    self.assertEqual(len(data["autoMode"]["allow"]), 3)
                    self.assertEqual((home / ".claude/model-backend").read_text().strip(), "codex")
                else:
                    self.assertEqual(data, original)
                    self.assertFalse((home / ".claude/model-backend").exists())
