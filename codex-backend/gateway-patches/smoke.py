#!/usr/bin/env python3
"""Validate a candidate gateway on a separate port and isolated native history."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--binary", required=True, type=Path)
parser.add_argument("--config", required=True, type=Path, help="Private JSON config of the desktop gateway")
parser.add_argument("--claude", type=Path, default=Path.home() / ".local/bin/claude")
parser.add_argument("--config-commentary", action="store_true", help="Use the upstream PR config option with the legacy environment flag removed")
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix="gateway-commentary-smoke-") as temp:
    root = Path(temp)
    try:
        config = json.loads(args.config.read_text())
    except json.JSONDecodeError:
        import yaml  # Optional only for validation of the VPS YAML config.
        config = yaml.safe_load(args.config.read_text())
    if args.config_commentary:
        config.setdefault("client", {}).setdefault("claude", {})["show-codex-commentary"] = True
    key = config["access"]["api-keys"][0]
    auths = root / "auths"
    auths.mkdir(mode=0o700)
    for source in Path(config["oauth"]["auth-dir"]).glob("*.json"):
        target = auths / source.name
        shutil.copyfile(source, target)
        target.chmod(0o600)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config["server"]["port"] = port
    config["oauth"]["auth-dir"] = str(auths)
    candidate = root / "config.json"
    candidate.write_text(json.dumps(config))
    candidate.chmod(0o600)
    env = os.environ.copy()
    if args.config_commentary:
        env.pop("CPA_VISIBLE_CODEX_COMMENTARY", None)
    else:
        env["CPA_VISIBLE_CODEX_COMMENTARY"] = "1"
    gateway = subprocess.Popen([str(args.binary), "-config", str(candidate)], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(50):
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/models", headers={"Authorization": "Bearer " + key})
                with urllib.request.urlopen(req, timeout=1) as response:
                    if any(item["id"] == "gpt-6.1-sol" for item in json.load(response)["data"]):
                        break
            except OSError:
                pass
            if gateway.poll() is not None:
                raise RuntimeError("Candidate gateway exited")
            time.sleep(0.2)
        else:
            raise RuntimeError("Candidate gateway did not become ready")
        native = root / "claude"
        native.mkdir(mode=0o700)
        settings = root / "settings.json"
        settings.write_text(json.dumps({"env": {
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}", "ANTHROPIC_AUTH_TOKEN": key,
            "ANTHROPIC_API_KEY": "", "CLAUDE_CODE_EFFORT_LEVEL": "high"}}))
        settings.chmod(0o600)
        env["CLAUDE_CONFIG_DIR"] = str(native)
        env.pop("CLAUDECODE", None)
        session = str(uuid.uuid4())
        command = [str(args.claude), "-p", "--output-format", "json", "--settings", str(settings),
                   "--setting-sources", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                   "--tools", "Bash", "--allowedTools", "Bash", "--max-turns", "5", "--model", "gpt-6.1-sol"]
        prompt = "Before using Bash, send a public progress message containing visible-progress-ok. Then use Bash to run printf tool-ok. Remember native-resume-ok for later and finish with final-ok."
        result = subprocess.run(command + ["--session-id", session, prompt], env=env, cwd=root,
                                text=True, capture_output=True, timeout=180)
        assert result.returncode == 0, "Native candidate call failed"
        assert not json.loads(result.stdout).get("is_error"), "Native candidate response error"
        history = next(native.rglob(session + ".jsonl"))
        blocks = []
        for line in history.read_text().splitlines():
            row = json.loads(line)
            if row.get("message", {}).get("role") == "assistant":
                blocks.extend(row["message"].get("content", []))
        comments = [b["thinking"] for b in blocks if isinstance(b, dict) and b.get("signature") == "cpa:commentary:v1"]
        texts = [b["text"] for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
        assert any("visible-progress-ok" in text for text in comments), "No actual commentary was generated"
        assert any("visible-progress-ok" in text for text in texts), "Public commentary is still hidden"
        assert any(b.get("type") == "tool_use" and b.get("name") == "Bash" for b in blocks if isinstance(b, dict)), "Bash was not called"
        print("PASS native streaming: progress visible, original phase preserved, Bash executed", flush=True)
        result = subprocess.run(command + ["-r", session, "What marker did I ask you to remember for later? Answer only with it."],
                                env=env, cwd=root, text=True, capture_output=True, timeout=180)
        assert result.returncode == 0 and "native-resume-ok" in json.loads(result.stdout).get("result", ""), "Native replay/resume failed"
        print("PASS native resume with commentary history and preserved context", flush=True)
    finally:
        gateway.terminate()
        try:
            gateway.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            gateway.kill()
            gateway.communicate()
