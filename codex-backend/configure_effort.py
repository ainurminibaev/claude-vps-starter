#!/usr/bin/env python3
"""Migrate our forced-High profile to client-selected effort without replacing unrelated config."""
import argparse
import importlib.util
import json
import os
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "desktop_backend", Path(__file__).resolve().parents[1] / "desktop-codex-backend/claude-backend.py")
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


def configure(data, revert=False):
    payload = data.setdefault("requests", {}).setdefault("payload", {})
    legacy = {"models": [{"name": backend.MODEL, "protocol": "codex"}],
              "params": {"reasoning.effort": "high"}}
    default = backend.client_effort_default()
    guards = [backend.auxiliary_effort_override(), backend.classifier_effort_override()]
    overrides = payload.setdefault("override", [])
    defaults = payload.setdefault("default", [])
    overrides[:] = [rule for rule in overrides if rule not in [legacy, *guards]]
    defaults[:] = [rule for rule in defaults if rule != default]
    if revert:
        overrides.insert(0, legacy)
        if not defaults:
            payload.pop("default", None)
    else:
        # Keep other operator rules and their relative ordering; stage one must
        # remain after the disabled-thinking guard so it can select Medium.
        defaults.append(default)
        overrides[:0] = guards
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
        print("Dry run: preserve explicit client effort; missing-field fallback Medium; auxiliary reviews High, recognized stage one Medium.")
        return
    updated = dump(configure(data, args.revert)).encode()
    if updated == original:
        print("Effort policy already configured; no changes.")
        return
    backup = path.with_name(path.name + ".before-client-effort")
    if not backup.exists():
        backend.private_write(backup, original)
    backend.private_write(path, updated)
    print("Forced High restored." if args.revert else "Client effort enabled. Config hot-reloads; existing requests and sessions continue.")


if __name__ == "__main__":
    main()
