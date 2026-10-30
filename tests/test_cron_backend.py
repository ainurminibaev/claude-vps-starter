import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "codex-backend/configure_cron.py"
spec = importlib.util.spec_from_file_location("cron_backend", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CronBackendTests(unittest.TestCase):
    def test_jobs_schedules_and_legacy_login_are_preserved(self):
        old = ("SHELL=/bin/bash\nBASH_ENV=/root/.claude/oauth-token-env\n"
               "* * * * * /root/claude-tg-watchdog.sh\n"
               "0 6 * * * /some/script.sh\n"
               "30 18 * * * /usr/bin/claude -p 'job' --model opus\n")
        new, count = module.transform_crontab(old)
        self.assertEqual(count, 1)
        self.assertIn("BASH_ENV=/usr/local/lib/claude-bot-cron/env.sh\n", new)
        self.assertIn("* * * * * /root/claude-tg-watchdog.sh\n", new)
        self.assertIn("0 6 * * * /some/script.sh\n", new)
        self.assertIn("30 18 * * * /usr/bin/claude -p 'job' --model opus\n", new)

    def test_update_is_idempotent_and_keeps_custom_paths(self):
        original = "PATH=/custom/bin:/usr/bin\n0 6 * * * claude -p 'test'\n"
        once, _ = module.transform_crontab(original)
        twice, count = module.transform_crontab(once)
        self.assertEqual(once, twice)
        self.assertEqual(count, 0)
        self.assertIn("/custom/bin:/usr/bin", twice)

    def test_another_users_legacy_login_and_task_are_preserved(self):
        job = "0 8 * * * /home/rafka/projects/personal/daily-allah-name/run.sh > /dev/null 2>&1\n"
        original = "BASH_ENV=/home/rafka/.claude/oauth-token-env\n" + job
        updated, _ = module.transform_crontab(original, "/home/rafka/.claude/oauth-token-env")
        self.assertIn(job, updated)
        self.assertIn("BASH_ENV=" + module.ENV_FILE, updated)


if __name__ == "__main__":
    unittest.main()
