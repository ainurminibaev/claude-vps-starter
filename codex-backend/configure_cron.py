#!/usr/bin/env python3
"""Route root's headless Claude cron jobs through its reversible backend."""
import argparse
from datetime import datetime, timezone
import os
import pwd
from pathlib import Path
import subprocess


ENV_FILE = "/usr/local/lib/claude-bot-cron/env.sh"


def transform_crontab(original, legacy_env="/root/.claude/oauth-token-env"):
    lines = original.splitlines()
    found_env = False
    model_calls = 0
    for i, line in enumerate(lines):
        if line.startswith("BASH_ENV="):
            found_env = True
            old = line.split("=", 1)[1].strip('"\'')
            if old not in (legacy_env, ENV_FILE):
                raise ValueError("Custom BASH_ENV found; refusing to replace it")
            lines[i] = "BASH_ENV=" + ENV_FILE
        elif line.strip() and not line.lstrip().startswith("#"):
            model_calls += line.count("/usr/bin/claude -p")
    if not found_env:
        lines.insert(0, "BASH_ENV=" + ENV_FILE)
    return "\n".join(lines) + "\n", model_calls


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--user", default="root")
    args = parser.parse_args()
    if os.getuid() != 0:
        raise SystemExit("Run as root")
    entry = pwd.getpwnam(args.user)
    original = subprocess.check_output(["crontab", "-u", args.user, "-l"], text=True)
    updated, count = transform_crontab(original, str(Path(entry.pw_dir) / ".claude/oauth-token-env"))
    print("cron user:", args.user)
    print("absolute model calls covered without command changes:", count)
    print("cron changes needed:", original != updated)
    if args.apply and updated != original:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = Path.home() / "backups" / ("crontab-before-codex-" + args.user + "-" + stamp)
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(original)
        backup.chmod(0o600)
        subprocess.run(["crontab", "-u", args.user, "-"], input=updated, text=True, check=True)
        print("backup:", backup)


if __name__ == "__main__":
    main()
