#!/usr/bin/env python3
"""Prepare root-only CPA config from an existing Codex login; print no secrets."""
import base64
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shlex
import pwd


def claims(token):
    data = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))


def private_write(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--users", nargs="+", default=["root"])
    args = parser.parse_args()
    if os.getuid() != 0:
        raise SystemExit("Run as root")
    home = Path.home()
    root = home / ".config/codex-model-proxy"
    auths = root / "auths"
    auths.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    auths.chmod(0o700)
    key_file = root / "client-key"
    key = key_file.read_text().strip() if key_file.exists() else secrets.token_urlsafe(32)
    private_write(key_file, key + "\n")
    clients = root / "clients"
    clients.mkdir(exist_ok=True, mode=0o700)
    users = list(dict.fromkeys(["root", *args.users, *(p.name for p in clients.iterdir() if p.is_file())]))
    keys = []
    for user in users:
        entry = pwd.getpwnam(user)
        stored = clients / user
        user_key = key if user == "root" else stored.read_text().strip() if stored.exists() else secrets.token_urlsafe(32)
        private_write(stored, user_key + "\n")
        keys.append(user_key)
        config_parent = Path(entry.pw_dir) / ".config"
        if not config_parent.exists():
            config_parent.mkdir(mode=0o700)
            os.chown(config_parent, entry.pw_uid, entry.pw_gid)
        client_dir = config_parent / "codex-model-proxy"
        client_dir.mkdir(exist_ok=True, mode=0o700)
        client_dir.chmod(0o700)
        os.chown(client_dir, entry.pw_uid, entry.pw_gid)
        client_env = client_dir / "client.env"
        private_write(client_env, "CODEX_PROXY_KEY=" + shlex.quote(user_key) + "\n")
        os.chown(client_env, entry.pw_uid, entry.pw_gid)
    template = Path(__file__).with_name("config.example.yaml").read_text()
    config_path = root / "config.yaml"
    compression_backup = root / "config.before-compression.yaml"
    if config_path.exists() and not compression_backup.exists():
        private_write(compression_backup, config_path.read_text())
    private_write(root / "config.yaml", template.replace('["__CLIENT_KEYS__"]', json.dumps(keys)))
    token_file = auths / "codex-root.json"
    # Preserve refreshes performed by CPA; importing again must not restore an old token.
    if not token_file.exists():
        original = json.loads((home / ".codex/auth.json").read_text())
        tokens = original["tokens"]
        access = claims(tokens["access_token"])
        identity = claims(tokens["id_token"])
        account = identity.get("https://api.openai.com/auth", {})
        expiry = datetime.fromtimestamp(access["exp"], timezone.utc).isoformat().replace("+00:00", "Z")
        storage = {**tokens, "type": "codex", "expired": expiry,
                   "last_refresh": original.get("last_refresh", ""),
                   "email": identity.get("email", ""),
                   "plan_type": account.get("chatgpt_plan_type", "")}
        private_write(token_file, json.dumps(storage) + "\n")
    print("Proxy config prepared for:", ", ".join(users))


if __name__ == "__main__":
    main()
