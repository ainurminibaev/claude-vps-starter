#!/usr/bin/env python3
"""Hot-reload only the measured Auto Mode stage-one effort policy; never print secrets."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile

spec = importlib.util.spec_from_file_location(
    "desktop_backend", Path(__file__).resolve().parents[1] / "desktop-codex-backend/claude-backend.py")
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


def configure(data, remove=False):
    policy = backend.auto_mode_override()
    rules = data.setdefault("requests", {}).setdefault("payload", {}).setdefault("override", [])
    rules[:] = [rule for rule in rules if rule != policy]
    if not remove:
        rules.append(policy)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--revert", action="store_true")
    args = parser.parse_args()
    path = args.config or Path.home() / (".config/claude-backend/config.yaml" if os.uname().sysname == "Darwin" else ".config/codex-model-proxy/config.yaml")
    original = path.read_bytes()
    try:
        data = json.loads(original)
        dump = lambda value: json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    except json.JSONDecodeError:
        import yaml
        data = yaml.safe_load(original)
        dump = lambda value: yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
    if not (args.apply or args.revert):
        print("Dry run: main High; recognized Auto Mode stage one Medium; stage two High.")
        return
    updated = dump(configure(data, args.revert)).encode()
    if updated == original:
        print("Auto Mode policy already configured; no changes.")
        return
    backup = path.with_name(path.name + ".before-auto-mode")
    if not backup.exists():
        backend.private_write(backup, original)
    fd, temporary = tempfile.mkstemp(prefix=".auto-mode-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(updated)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("Auto Mode policy removed; main High." if args.revert else "Auto Mode stage one Medium; main and stage two High. Config hot-reload; clients unchanged.")


if __name__ == "__main__":
    main()
