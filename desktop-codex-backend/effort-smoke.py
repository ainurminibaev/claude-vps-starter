#!/usr/bin/env python3
"""Inspect native Claude effort requests against a loopback stub, with no model calls or saved sessions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Stub(BaseHTTPRequestHandler):
    captures = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"data":[]}')

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if "count_tokens" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"input_tokens":10}')
            return
        self.captures.append({"model": data.get("model"), "thinking": data.get("thinking"),
                              "effort": data.get("output_config", {}).get("effort"),
                              "request_class": self.headers.get("x-claude-code-request-class"),
                              "system_chars": len(json.dumps(data.get("system", [])))})
        response = {"id": "msg_effort_probe", "type": "message", "role": "assistant",
                    "model": data.get("model"), "content": [{"type": "text", "text": "OK"}],
                    "stop_reason": "end_turn", "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 1}}
        self.send_response(200)
        if data.get("stream"):
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            start = {**response, "content": [], "stop_reason": None}
            events = [("message_start", {"message": start}),
                      ("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
                      ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "OK"}}),
                      ("content_block_stop", {"index": 0}),
                      ("message_delta", {"delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 1}}),
                      ("message_stop", {})]
            for kind, fields in events:
                self.wfile.write(("event: " + kind + "\ndata: " + json.dumps({"type": kind, **fields}) + "\n\n").encode())
            self.wfile.flush()
        else:
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())


def main():
    binary = shutil.which("claude")
    if not binary:
        raise SystemExit("Native claude binary not found")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="native-effort-smoke-") as temp:
            root = Path(temp)
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith("ANTHROPIC_") and k not in
                   ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_EFFORT_LEVEL")}
            env.update(CLAUDE_CONFIG_DIR=str(root / "native"),
                       ANTHROPIC_BASE_URL=f"http://127.0.0.1:{server.server_port}",
                       ANTHROPIC_AUTH_TOKEN="loopback-probe", ANTHROPIC_API_KEY="",
                       CLAUDE_CODE_AUTO_MODE_SERVER="0", CLAUDE_CODE_GATEWAY_HINT_HEADERS="1",
                       CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_AUTOUPDATER="1")
            for model, effort in [("claude-opus-5-5", None), ("claude-opus-5-5", "medium"),
                                  ("claude-opus-5-5", "high"), ("claude-opus-5-5", "low"),
                                  ("gpt-6.1-sol", "medium")]:
                Stub.captures.clear()
                command = [binary, "-p", "--model", model, "--output-format", "json",
                           "--setting-sources", "", "--strict-mcp-config", "--mcp-config",
                           '{"mcpServers":{}}', "--tools", "", "--max-turns", "1",
                           "--no-session-persistence"]
                if effort:
                    command += ["--effort", effort]
                command += ["Reply with exactly OK."]
                run = subprocess.run(command, env=env, cwd=root, text=True, capture_output=True, timeout=45)
                print(json.dumps({"requested_model": model, "requested_effort": effort or "default",
                                  "exit": run.returncode, "requests": Stub.captures}, ensure_ascii=False), flush=True)
                if run.returncode or not Stub.captures:
                    raise SystemExit("Native loopback effort probe failed; no external inference was requested")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
