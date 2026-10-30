#!/usr/bin/env python3
"""Mac profile switch for the unmodified Claude Code CLI. Never print credentials."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import secrets
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

VERSION = "8.0.47"
ARCHIVE = f"CLIProxyAPI_{VERSION}_darwin_aarch64.tar.gz"
SHA256 = "a5b260ec0f843d2291488acd76e5595232da8f3b8ec2d334154f1570f5f3ca08"
LABEL = "com.ainur.claude-codex-proxy"
URL = "http://127.0.0.1:18418"
MODEL = "gpt-6.1-sol"
ALIASES = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5",
           # Auto Mode deliberately uses Sonnet 5 instead of the Sonnet 5.5 alias.
           "claude-sonnet-5",
           "claude-opus-4-6", "claude-sonnet-4-6", "claude-opus-4-5-20251101",
           "claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001"]
MANAGED = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
           "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
           "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_EFFORT_LEVEL",
           "CLAUDE_CODE_AUTO_COMPACT_WINDOW")


def private_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".claude-backend-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data if isinstance(data, bytes) else data.encode())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    private_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def codex_env(key):
    return {"ANTHROPIC_BASE_URL": URL, "ANTHROPIC_AUTH_TOKEN": key,
            # Prevent a saved API key from taking precedence over the loopback token.
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": ALIASES[0],
            "ANTHROPIC_DEFAULT_SONNET_MODEL": ALIASES[1],
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": ALIASES[2],
            "CLAUDE_CODE_EFFORT_LEVEL": "high",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "475000"}


def activate(settings, state, env):
    settings = json.loads(json.dumps(settings))
    state = json.loads(json.dumps(state))
    if state.get("active") != "codex":
        original = settings.get("env", {})
        state["baseline"] = {k: original[k] for k in MANAGED if k in original}
        state["env_existed"] = "env" in settings
    settings.setdefault("env", {}).update(env)
    state["active"] = "codex"
    return settings, state


def deactivate(settings, state):
    settings = json.loads(json.dumps(settings))
    state = json.loads(json.dumps(state))
    if state.get("active") == "codex":
        env = settings.get("env", {})
        for key in MANAGED:
            env.pop(key, None)
        env.update(state.get("baseline", {}))
        if not env and not state.get("env_existed"):
            settings.pop("env", None)
        state["active"] = "claude"
    return settings, state


class Backend:
    def __init__(self, home=None):
        self.home = Path(home) if home else Path.home()
        self.root = self.home / ".config/claude-backend"
        self.binary = self.home / ".local/share/claude-backend/cli-proxy-api"
        self.settings = self.home / ".claude/settings.json"
        self.state = self.root / "state.json"
        self.plist = self.home / f"Library/LaunchAgents/{LABEL}.plist"
        self.service = f"gui/{os.getuid()}/{LABEL}"

    def key(self):
        return (self.root / "client-key").read_text().strip()

    def health(self):
        try:
            req = urllib.request.Request(URL + "/v1/models", headers={"Authorization": "Bearer " + self.key()})
            with urllib.request.urlopen(req, timeout=2) as response:
                ids = {item["id"] for item in json.load(response)["data"]}
            return MODEL in ids and set(ALIASES) <= ids
        except (OSError, ValueError, KeyError, urllib.error.URLError):
            return False

    def install(self, archive=None, proxy_binary=None):
        if os.uname().sysname != "Darwin" or os.uname().machine != "arm64":
            raise ValueError("This installer supports Apple Silicon macOS only")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        auths = self.root / "auths"
        auths.mkdir(exist_ok=True, mode=0o700)
        auths.chmod(0o700)
        if proxy_binary:
            if self.binary.exists():
                backup = self.binary.with_name("cli-proxy-api.before-commentary")
                if not backup.exists():
                    private_write(backup, self.binary.read_bytes())
                    backup.chmod(0o700)
            private_write(self.binary, Path(proxy_binary).read_bytes())
            self.binary.chmod(0o700)
        if not self.binary.exists():
            with tempfile.TemporaryDirectory(prefix="claude-backend-") as tmp:
                asset = Path(archive) if archive else Path(tmp) / ARCHIVE
                if not archive:
                    subprocess.run(["/usr/bin/curl", "--fail", "--silent", "--show-error", "--location",
                                    "--max-time", "180", "--output", str(asset),
                                    f"https://github.com/router-for-me/CLIProxyAPI/releases/download/v{VERSION}/{ARCHIVE}"], check=True)
                if hashlib.sha256(asset.read_bytes()).hexdigest() != SHA256:
                    raise ValueError("CLIProxyAPI archive checksum mismatch")
                with tarfile.open(asset) as tar:
                    member = tar.getmember("cli-proxy-api")
                    if not member.isfile():
                        raise ValueError("Invalid executable in archive")
                    private_write(self.binary, tar.extractfile(member).read())
                self.binary.chmod(0o700)
        key_file = self.root / "client-key"
        if not key_file.exists():
            private_write(key_file, secrets.token_urlsafe(32) + "\n")
        target = auths / "codex-local.json"
        # Keep tokens refreshed by the proxy on repeated installs.
        if not target.exists():
            original = read_json(self.home / ".codex/auth.json")
            tokens = original["tokens"]
            def claims(token):
                part = token.split(".")[1]
                return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
            access, identity = claims(tokens["access_token"]), claims(tokens["id_token"])
            write_json(target, {**tokens, "type": "codex",
                       "expired": datetime.fromtimestamp(access["exp"], timezone.utc).isoformat().replace("+00:00", "Z"),
                       "last_refresh": original.get("last_refresh", ""),
                       "email": identity.get("email", ""),
                       "plan_type": identity.get("https://api.openai.com/auth", {}).get("chatgpt_plan_type", "")})
        config = {"config-version": 8, "server": {"host": "127.0.0.1", "port": 18418},
                  "management": {"allow-remote": False, "disable-control-panel": True, "disable-auto-update-panel": True},
                  "access": {"api-keys": [self.key()]},
                  "oauth": {"auth-dir": str(auths), "model-alias": {"codex": [
                      {"name": MODEL, "alias": alias, "fork": True} for alias in ALIASES]}},
                  "routing": {"session-affinity": True, "retry": {"request-retry": 3, "max-retry-interval": 30}},
                  "upstream": {"codex": {"upstream-request-compression": True,
                                         "upstream-request-compression-min-bytes": 1024}},
                  "requests": {"tls-handshake-timeout-seconds": 20, "nonstream-keepalive-interval": 10,
                      "streaming": {"keepalive-seconds": 15, "bootstrap-retries": 2},
                      "payload": {"override": [{"models": [{"name": MODEL, "protocol": "codex"}],
                                                "params": {"reasoning.effort": "high"}}]}},
                  "observability": {"logs": {"debug": False, "logging-to-file": False}}}
        # JSON is a YAML subset, understood by CLIProxyAPI's YAML loader.
        config_path = self.root / "config.yaml"
        compression_backup = self.root / "config.before-compression.json"
        if config_path.exists() and not compression_backup.exists():
            private_write(compression_backup, config_path.read_bytes())
        write_json(config_path, config)
        logdir = self.home / "Library/Logs/claude-backend"
        logdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        logdir.chmod(0o700)
        plist = {"Label": LABEL, "ProgramArguments": [str(self.binary), "-config", str(self.root / "config.yaml")],
                 "WorkingDirectory": str(self.root), "RunAtLoad": True, "KeepAlive": True,
                 "ThrottleInterval": 10, "StandardOutPath": str(logdir / "proxy.log"),
                 "StandardErrorPath": str(logdir / "proxy-error.log"),
                 "EnvironmentVariables": {"HOME": str(self.home), "CPA_VISIBLE_CODEX_COMMENTARY": "1"}}
        private_write(self.plist, plistlib.dumps(plist))
        command = self.home / ".local/bin/claude-backend"
        if Path(__file__).resolve() != command.resolve():
            private_write(command, Path(__file__).read_bytes())
            command.chmod(0o700)
        print("Installed local gateway and claude-backend; native Claude unchanged.")

    def start(self):
        subprocess.run(["launchctl", "enable", self.service], check=True)
        loaded = subprocess.run(["launchctl", "print", self.service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if not loaded:
            subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(self.plist)], check=True)
        for _ in range(30):
            if self.health():
                return
            time.sleep(0.3)
        raise ValueError("Local gateway is not healthy; see ~/Library/Logs/claude-backend")

    def switch(self, profile):
        settings = read_json(self.settings, {})
        state = read_json(self.state, {})
        if profile == "codex":
            self.start()
            if state.get("active") != "codex":
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                write_json(self.root / "backups" / f"settings-{stamp}.json", settings)
            settings, newstate = activate(settings, state, codex_env(self.key()))
            # Save the baseline first, so an interrupted write is recoverable.
            write_json(self.state, newstate)
            write_json(self.settings, settings)
        else:
            settings, newstate = deactivate(settings, state)
            write_json(self.settings, settings)
            write_json(self.state, newstate)
            subprocess.run(["launchctl", "bootout", self.service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["launchctl", "disable", self.service], check=True)
        self.status()
        print("Profile applies to newly started Claude processes. Restart existing sessions with claude -r.")

    def status(self):
        state = read_json(self.state, {})
        actual = read_json(self.settings, {}).get("env", {}).get("ANTHROPIC_BASE_URL") == URL
        print("Profile:", "codex" if actual else "claude")
        if actual:
            print(f"Backend: {MODEL}, reasoning high; opus/sonnet/haiku are compatibility aliases.")
            print("Local gateway:", "ready" if self.health() else "unavailable")
        elif state.get("active") == "codex":
            print("Settings changed externally; saved original environment is available for restoration.")
        print("Native CLI:", self.home / ".local/bin/claude")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["install", "codex", "claude", "status", "start"])
    parser.add_argument("--archive", type=Path, help="Previously downloaded, checksum-verified during install")
    parser.add_argument("--proxy-binary", type=Path, help="Locally built, patched gateway executable")
    args = parser.parse_args()
    backend = Backend()
    try:
        if args.command == "install":
            backend.install(args.archive, args.proxy_binary)
        elif args.command in ("codex", "claude"):
            backend.switch(args.command)
        elif args.command == "start":
            backend.start()
        else:
            backend.status()
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        # Credential/file parse errors must never include their contents.
        parser.exit(1, f"claude-backend failed ({type(error).__name__}); check installation and private config files.\n")


if __name__ == "__main__":
    main()
