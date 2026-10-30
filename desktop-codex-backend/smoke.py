#!/usr/bin/env python3
"""Opt-in live smoke test; uses isolated Claude history/settings and real Codex calls."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

spec = importlib.util.spec_from_file_location("backend", Path(__file__).with_name("claude-backend.py"))
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)
local = backend.Backend()
if not local.health():
    raise SystemExit("Start the local gateway first")
with tempfile.TemporaryDirectory(prefix="claude-backend-smoke-") as tmp:
    root = Path(tmp)
    config = root / "config"
    config.mkdir(mode=0o700)
    settings = root / "settings.json"
    backend.write_json(settings, {"env": backend.codex_env(local.key())})
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_") and k not in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID")}
    env["CLAUDE_CONFIG_DIR"] = str(config)
    cli = [str(Path.home() / ".local/bin/claude"), "-p", "--output-format", "json",
           "--settings", str(settings), "--setting-sources", "", "--strict-mcp-config",
           "--mcp-config", '{"mcpServers":{}}', "--tools", "Bash", "--allowedTools", "Bash",
           "--max-turns", "6"]
    session = str(uuid.uuid4())
    marker = "remember-" + uuid.uuid4().hex

    def call(args):
        result = subprocess.run(cli + args, env=env, cwd=root, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        if result.returncode:
            # Local key and access tokens must never enter diagnostic output.
            safe = result.stdout.replace(local.key(), "[redacted]")[-1200:]
            raise RuntimeError(f"Claude exit {result.returncode}: {safe}")
        data = json.loads(result.stdout)
        if data.get("is_error"):
            raise RuntimeError("Claude returned an error: " + str(data.get("result", ""))[:500])
        return data

    first = call(["--model", "opus", "--session-id", session, f"Remember this exact marker for later: {marker}. Use Bash to run printf desktop-tool-ok, then answer with its output and the marker."])
    assert marker in first["result"] and "desktop-tool-ok" in first["result"], "First response missing sentinel"
    history = list(config.rglob(session + ".jsonl"))
    assert history, "Native session was not persisted"
    records = [json.loads(line) for line in history[0].read_text().splitlines()]
    assert any(block.get("type") == "tool_use" and block.get("name") == "Bash"
               for row in records for block in row.get("message", {}).get("content", []) if isinstance(block, dict)), "No actual Bash tool call"
    print("PASS native claude --model opus: Codex response and real Bash tool call", flush=True)
    resumed = call(["-r", session, "What exact marker did I ask you to remember? Answer only with it."])
    assert marker in resumed["result"], "Resume lost the conversation marker"
    print("PASS native claude -r: same session and remembered conversation", flush=True)
