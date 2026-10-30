#!/usr/bin/env python3
"""Install a validated candidate at a quiet request boundary; keep rollback copies."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
import time
import urllib.request


def atomic(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".gateway-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def pending_requests(lines):
    pending = set()
    for line in lines:
        match = re.search(r'\[[^]]+\] \[([0-9a-f]{8})\]', line)
        if not match:
            continue
        request = match.group(1)
        if "session-affinity: cache hit" in line:
            pending.add(request)
        if "[gin_logger.go:" in line:
            pending.discard(request)
    return len(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--wait-idle", type=int, default=180)
    args = parser.parse_args()
    data = args.binary.read_bytes()
    if hashlib.sha256(data).hexdigest() != args.sha256 or b"8.0.47-itab.1" not in data:
        raise SystemExit("Candidate checksum or build identifier mismatch")
    home = Path.home()
    mac = os.uname().sysname == "Darwin"
    if not mac and os.getuid() != 0:
        raise SystemExit("Run Linux installation as root")
    if mac:
        target = home / ".local/share/claude-backend/cli-proxy-api"
        settings = home / "Library/LaunchAgents/com.ainur.claude-codex-proxy.plist"
        keyfile = home / ".config/claude-backend/client-key"
        service = f"gui/{os.getuid()}/com.ainur.claude-codex-proxy"
        log = home / "Library/Logs/claude-backend/proxy.log"
        def logs():
            return log.read_text().splitlines()
        def restart():
            subprocess.run(["launchctl", "enable", service], check=True)
            subprocess.run(["launchctl", "bootout", service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # launchd may keep the just-unloaded job briefly; bootstrap is then
            # rejected with EIO even though the executable and plist are valid.
            for _ in range(40):
                result = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(settings)],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if result.returncode == 0:
                    return
                time.sleep(0.25)
            raise RuntimeError("launchd did not accept the gateway after unload")
        config = plistlib.loads(settings.read_bytes())
        config.setdefault("EnvironmentVariables", {})["CPA_VISIBLE_CODEX_COMMENTARY"] = "1"
        new_settings = plistlib.dumps(config)
        settings_mode = 0o600
    else:
        target = home / ".local/bin/cli-proxy-api"
        settings = Path("/etc/systemd/system/codex-model-proxy.service.d/commentary.conf")
        keyfile = home / ".config/codex-model-proxy/client-key"
        def logs():
            return subprocess.run(["journalctl", "-u", "codex-model-proxy", "--no-pager", "-o", "cat", "-n", "50000"],
                                  capture_output=True, text=True, check=True).stdout.splitlines()
        def restart():
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "restart", "codex-model-proxy"], check=True)
        new_settings = b"[Service]\nEnvironment=CPA_VISIBLE_CODEX_COMMENTARY=1\n"
        settings_mode = 0o644
    deadline = time.monotonic() + args.wait_idle
    previous = None
    while True:
        count = pending_requests(logs())
        if count == 0:
            break
        if count != previous:
            print("Waiting for model requests:", count, flush=True)
            previous = count
        if time.monotonic() >= deadline:
            raise SystemExit("Gateway still busy; candidate not installed. Retry later.")
        time.sleep(0.3)
    original = target.read_bytes()
    original_settings = settings.read_bytes() if settings.exists() else None
    original_mode = target.stat().st_mode & 0o777
    backup = target.with_name("cli-proxy-api.before-commentary")
    if not backup.exists():
        atomic(backup, original, original_mode)
    try:
        atomic(target, data, original_mode)
        atomic(settings, new_settings, settings_mode)
        restart()
        key = keyfile.read_text().strip()
        for _ in range(50):
            try:
                req = urllib.request.Request("http://127.0.0.1:18418/v1/models", headers={"Authorization": "Bearer " + key})
                with urllib.request.urlopen(req, timeout=2) as response:
                    if any(model["id"] == "gpt-6.1-sol" for model in json.load(response)["data"]):
                        print("Gateway updated: 8.0.47-itab.1; public commentary enabled; model ready.")
                        return
            except OSError:
                pass
            time.sleep(0.2)
        raise RuntimeError("Gateway health check failed")
    except Exception:
        atomic(target, original, original_mode)
        if original_settings is None:
            settings.unlink(missing_ok=True)
        else:
            atomic(settings, original_settings, settings_mode)
        restart()
        raise


if __name__ == "__main__":
    main()
