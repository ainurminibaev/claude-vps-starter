"""Check backend switching without contacting models or Telegram."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


LAUNCHER = Path(__file__).resolve().parents[1] / "scripts/claude-bot-launch.sh"


class BotLaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / ".claude").mkdir()
        bindir = self.home / "bin"
        bindir.mkdir()
        fake = bindir / "claude"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json,os,sys\n"
            "keys=['CLAUDE_CODE_OAUTH_TOKEN','ANTHROPIC_API_KEY',"
            "'ANTHROPIC_AUTH_TOKEN','ANTHROPIC_BASE_URL','ANTHROPIC_MODEL','CLAUDE_CODE_AUTO_MODE_SERVER']\n"
            "print(json.dumps({'args':sys.argv[1:],"
            "'env':{k:os.environ.get(k) for k in keys}}))\n"
            "sys.exit(int(os.environ.get('FAKE_EXIT','0')))\n"
        )
        fake.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update(HOME=str(self.home), PATH=f"{bindir}:{self.env['PATH']}")
        self.env["CLAUDE_BOT_CLI_BIN"] = str(fake)
        self.env["CLAUDE_BOT_ENV_FILE"] = str(LAUNCHER.with_name("claude-bot-env.sh"))
        config = self.home / ".config/codex-model-proxy"
        config.mkdir(parents=True)
        (config / "client.env").write_text("CODEX_PROXY_KEY=test-local-key\n")

    def launch(self, *args):
        return subprocess.run(
            ["bash", str(LAUNCHER), *args], env=self.env,
            text=True, capture_output=True, check=False,
        )

    def test_default_keeps_normal_claude_login_and_model(self):
        # Returning from an inherited gateway environment restores native behavior.
        self.env.update(ANTHROPIC_BASE_URL="http://127.0.0.1:18418", CLAUDE_CODE_AUTO_MODE_SERVER="0")
        (self.home / ".claude/oauth-token-env").write_text(
            "export CLAUDE_CODE_OAUTH_TOKEN=saved-claude-login\n"
        )
        r = self.launch("--resume", "same-session", "--model", "opus")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["args"], ["--resume", "same-session", "--model", "opus"])
        self.assertEqual(data["env"]["CLAUDE_CODE_OAUTH_TOKEN"], "saved-claude-login")
        self.assertIsNone(data["env"]["CLAUDE_CODE_AUTO_MODE_SERVER"])

    def test_codex_isolates_credentials_and_preserves_session_arguments(self):
        (self.home / ".claude/model-backend").write_text("codex\n")
        self.env.update(CLAUDE_CODE_OAUTH_TOKEN="old-login", ANTHROPIC_API_KEY="old-key")
        r = self.launch("--resume", "same-session", "--model", "opus", "a prompt; literal")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["args"], ["--model", "gpt-6.1-sol", "--resume", "same-session", "a prompt; literal"])
        self.assertIsNone(data["env"]["CLAUDE_CODE_OAUTH_TOKEN"])
        self.assertIsNone(data["env"]["ANTHROPIC_API_KEY"])
        self.assertEqual(data["env"]["ANTHROPIC_BASE_URL"], "http://127.0.0.1:18418")
        self.assertEqual(data["env"]["ANTHROPIC_AUTH_TOKEN"], "test-local-key")
        self.assertEqual(data["env"]["CLAUDE_CODE_AUTO_MODE_SERVER"], "0")

    def test_real_child_output_and_exit_status_are_preserved(self):
        (self.home / ".claude/model-backend").write_text("codex\n")
        self.env["FAKE_EXIT"] = "7"
        r = self.launch("-p", "--model=opus", "test")
        self.assertEqual(r.returncode, 7)
        self.assertTrue(json.loads(r.stdout)["args"])

    def test_invalid_backend_does_not_launch_cli(self):
        (self.home / ".claude/model-backend").write_text("typo\n")
        r = self.launch()
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
