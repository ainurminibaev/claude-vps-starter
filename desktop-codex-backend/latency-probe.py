#!/usr/bin/env python3
"""Opt-in paired latency probe. Credentials stay in memory; no tool execution."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import subprocess
import tempfile
import time
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--repeats", type=int, default=2)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--matched-tools", action="store_true", help="Match the image-generation tool injected by CPA")
parser.add_argument("--image-ab", action="store_true", help="Four direct code requests with image tool off/on/on/off")
parser.add_argument("--verify-image-tools", action="store_true", help="Verify the live gateway preserves explicit image tools without injecting them")
args = parser.parse_args()
root = Path.home() / ".config/claude-backend"
key = (root / "client-key").read_text().strip()
auth = json.loads((root / "auths/codex-local.json").read_text())
session = str(uuid.uuid4())
prompts = {
    "short": "Reply exactly latency-ok.",
    "code": "Write only JavaScript code, at most 250 words: implement mergeRanges(ranges), merging overlapping inclusive integer intervals. Do not mutate the input. Sort the result. Ignore reversed ranges. Include 4 assert tests for overlap, nested intervals, disjoint intervals, and empty input. No tools.",
}
results = []


def probe(route, case, prompt):
    payload = {"model": "gpt-6.1-sol", "instructions": "", "store": False, "stream": True,
               "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
               "reasoning": {"effort": "high", "summary": "auto"}, "tools": [],
               "prompt_cache_key": session, "text": {"verbosity": "low"}}
    if args.matched_tools:
        payload["tools"] = [{"type": "image_generation", "output_format": "png"}]
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if route == "direct":
        url = "https://chatgpt.com/backend-api/codex/responses"
        headers.update({"Authorization": "Bearer " + auth["access_token"],
                        "Chatgpt-Account-Id": auth["account_id"], "Session-Id": session,
                        "Originator": "codex-tui",
                        "User-Agent": "codex-tui/0.154.0 (Mac OS 26.5.2; arm64) iTerm.app/3.6.11 (codex-tui; 0.154.0)"})
    elif route == "proxy-responses":
        url = "http://127.0.0.1:18418/v1/responses"
        headers["Authorization"] = "Bearer " + key
    else:
        url = "http://127.0.0.1:18418/v1/messages"
        headers.update({"Authorization": "Bearer " + key, "anthropic-version": "2023-06-01"})
        payload = {"model": "claude-opus-5-5", "stream": True, "max_tokens": 2000,
                   "messages": [{"role": "user", "content": prompt}],
                   "output_config": {"effort": "high"}}
    with tempfile.TemporaryDirectory(prefix="latency-probe-") as temp:
        body = Path(temp) / "body.json"
        body.write_text(json.dumps(payload))
        body.chmod(0o600)
        config = ["url = " + json.dumps(url), "data-binary = " + json.dumps("@" + str(body))]
        config += ["header = " + json.dumps(name + ": " + value) for name, value in headers.items()]
        started = time.monotonic()
        process = subprocess.Popen(["/usr/bin/curl", "--silent", "--show-error", "--no-buffer", "--max-time", "120",
                                    "--http1.1", "--config", "-", "--write-out", "\nBENCH_META:%{json}\n"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.stdin.write(("\n".join(config) + "\n").encode())
        process.stdin.close()
        first_event = first_generated = first_text = None
        usage = {}
        metric = {}
        text = ""
        error_event = None
        response_meta = {}
        for raw in process.stdout:
            elapsed = time.monotonic() - started
            if raw.startswith(b"BENCH_META:"):
                metric = json.loads(raw[len(b"BENCH_META:"):])
                continue
            if not raw.startswith(b"data:"):
                continue
            try:
                event = json.loads(raw[5:].strip())
            except ValueError:
                continue
            if first_event is None:
                first_event = elapsed
            kind = event.get("type", "")
            if kind in ["response.output_text.delta", "response.reasoning_summary_text.delta", "response.function_call_arguments.delta"]:
                if first_generated is None:
                    first_generated = elapsed
            if kind == "response.output_text.delta":
                if first_text is None:
                    first_text = elapsed
                text += event.get("delta", "")
            if kind == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") in ["text_delta", "thinking_delta", "input_json_delta"] and first_generated is None:
                    first_generated = elapsed
                if delta.get("type") == "text_delta":
                    if first_text is None:
                        first_text = elapsed
                    text += delta.get("text", "")
            if kind == "response.completed":
                usage = event.get("response", {}).get("usage", {})
                response_meta = {k: event.get("response", {}).get(k) for k in ["model", "service_tier", "reasoning"]}
                response_meta["image_tool_present"] = any(tool.get("type") == "image_generation" for tool in event.get("response", {}).get("tools", []))
            if kind in ["message_start", "message_delta"]:
                usage.update(event.get("message", {}).get("usage", {}))
                usage.update(event.get("usage", {}))
            if kind == "error":
                error_event = event.get("error", {}).get("type", "error")
        process.wait()
        process.stderr.read()  # Never emit raw network error bodies or credential-bearing headers.
        result = {"route": route, "case": case, "curl_exit": process.returncode,
                  "image_tool": args.matched_tools,
                  "http_status": metric.get("http_code"), "error_event": error_event,
                  "tcp_s": metric.get("time_connect"), "tls_s": metric.get("time_appconnect"),
                  "pretransfer_s": metric.get("time_pretransfer"), "headers_s": metric.get("time_starttransfer"),
                  "first_event_s": first_event, "first_generated_s": first_generated,
                  "first_text_s": first_text, "total_s": metric.get("time_total"),
                  "input_tokens": usage.get("input_tokens"), "cached_input_tokens": usage.get("input_tokens_details", {}).get("cached_tokens", usage.get("cache_read_input_tokens")),
                  "output_tokens": usage.get("output_tokens"),
                  "reasoning_tokens": usage.get("output_tokens_details", {}).get("reasoning_tokens", usage.get("output_tokens_details", {}).get("thinking_tokens")),
                  "text_chars": len(text)}
        result["response_meta"] = response_meta
        print(json.dumps(result), flush=True)
        results.append(result)


if args.verify_image_tools:
    for enabled in [False, True]:
        args.matched_tools = enabled
        probe("proxy-responses", "image-tool-" + ("explicit" if enabled else "absent"), prompts["short"])
        assert results[-1]["response_meta"].get("image_tool_present") == enabled, "Image tool pass-through check failed"
elif args.image_ab:
    for enabled in [False, True, True, False]:
        args.matched_tools = enabled
        probe("direct", "code-image-" + ("on" if enabled else "off"), prompts["code"])
else:
    for case, prompt in prompts.items():
        for repeat in range(args.repeats):
            routes = ["direct", "proxy-responses", "proxy-claude"]
            if repeat % 2:
                routes.reverse()
            for route in routes:
                probe(route, case, prompt)
report = {"measured_at": datetime.now(timezone.utc).isoformat(), "model": "gpt-6.1-sol", "effort": "high",
          "notes": ["Same prompts; optional image tool; no tool execution; alternate order; small sample.",
                    "Direct uses curl TLS/HTTP1.1; gateway uses its own upstream transport.",
                    "This comparison is end-to-end, not a pure measurement of conversion CPU cost.",
                    "Tiny synthetic requests cannot establish latency of a 250k-token live session."], "results": results}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, indent=2) + "\n")
args.output.chmod(0o600)
for case in prompts:
    for route in ["direct", "proxy-responses", "proxy-claude"]:
        rows = [r for r in results if r["case"] == case and r["route"] == route and r.get("first_text_s") is not None]
        if rows:
            print("MEDIAN", case, route, "first_text_s", round(statistics.median(r["first_text_s"] for r in rows), 2),
                  "total_s", round(statistics.median(r["total_s"] for r in rows), 2), flush=True)
