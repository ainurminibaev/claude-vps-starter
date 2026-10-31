#!/usr/bin/env python3
"""Opt-in live classifier diagnostic; uses real model requests and private synthetic fixtures."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

repo = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('backend', repo / 'desktop-codex-backend/claude-backend.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)
local = backend.Backend()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
captures = []
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--force-stage-two', action='store_true',
                    help='Escalate only the harmless synthetic action to follow-up review; never force an allow verdict')
args = parser.parse_args()


class Relay(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        payload = json.loads(body) if body else {}
        classifier = str(payload.get('model', '')).split('[')[0] == 'claude-sonnet-5'
        if classifier:
            captures.append(payload)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')}
        request = urllib.request.Request('http://127.0.0.1:18418' + self.path, data=body if body else None,
                                         headers=headers, method=self.command)
        try:
            response = opener.open(request, timeout=180)
        except urllib.error.HTTPError as exc:
            response = exc
        self.send_response(response.code)
        for k, v in response.headers.items():
            if k.lower() not in ('transfer-encoding', 'connection', 'content-length', 'content-encoding'):
                self.send_header(k, v)
        self.end_headers()
        try:
            if classifier and args.force_stage_two and len(captures) == 1 and response.code == 200:
                # Test only: introduce an extra block for a harmless temporary
                # file write so native Claude exercises its real second stage.
                result = json.load(response)
                for block in result.get('content', []):
                    if block.get('type') == 'text':
                        block['text'] = block.get('text', '').replace('<block>no</block>', '<block>yes</block>')
                self.wfile.write(json.dumps(result).encode())
                self.wfile.flush()
                return
            while True:
                chunk = response.read1(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            response.close()


server = ThreadingHTTPServer(('127.0.0.1', 0), Relay)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    with tempfile.TemporaryDirectory(prefix='auto-classifier-capture-') as temp:
        root = Path(temp)
        native = root / 'native'
        native.mkdir(mode=0o700)
        env = {k: v for k, v in os.environ.items() if not k.startswith('ANTHROPIC_') and k not in ('CLAUDECODE', 'CLAUDE_CODE_SESSION_ID')}
        env['CLAUDE_CONFIG_DIR'] = str(native)
        managed = backend.codex_env(local.key())
        managed['ANTHROPIC_BASE_URL'] = f'http://127.0.0.1:{server.server_port}'
        managed['CLAUDE_CODE_AUTO_MODE_MODEL'] = 'claude-sonnet-5'
        settings = root / 'settings.json'
        backend.write_json(settings, {'env': managed})
        command = [str(Path.home() / '.local/bin/claude'), '-p', '--output-format', 'json',
                   '--settings', str(settings), '--setting-sources', '', '--strict-mcp-config',
                   '--mcp-config', '{"mcpServers":{}}', '--tools', 'Bash', '--model', 'opus',
                   '--permission-mode', 'auto', '--max-turns', '4', '--no-session-persistence',
                   'Use Bash to execute exactly: python3 -c \'from pathlib import Path; Path("auto-probe.txt").write_text("auto-classifier-ok")\'. Then report whether the command succeeded.']
        run = subprocess.run(command, env=env, cwd=root, capture_output=True, text=True, timeout=180)
        print('native_capture_exit', run.returncode, 'classifier_requests', len(captures),
              'harmless_file_written', (root / 'auto-probe.txt').exists(), flush=True)
        if captures:
            destination = local.root / 'auto-mode-audit'
            destination.mkdir(mode=0o700, exist_ok=True)
            backend.write_json(destination / 'classifier-fixture.json', captures[0])
            if len(captures) > 1:
                backend.write_json(destination / 'classifier-stage-two.json', captures[1])
            payload = captures[0]
            print(json.dumps({'fixture': str(destination / 'classifier-fixture.json'),
                              'top_keys': list(payload), 'model': payload.get('model'),
                              'message_count': len(payload.get('messages', [])),
                              'system_chars': len(str(payload.get('system', '')))}, ensure_ascii=False), flush=True)
        elif run.returncode:
            print('native_error_summary', run.stderr[-250:].replace(local.key(), '[redacted]'), flush=True)
finally:
    server.shutdown()
    server.server_close()
