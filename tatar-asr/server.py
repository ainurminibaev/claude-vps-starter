#!/usr/bin/env python3
"""Tatar ASR HTTP service (omniASR-CTC-300M-v2, int8 dynamic quantization).

POST /asr      multipart field `audio_file` (any format ffmpeg can decode) -> text/plain
               (raw body with any other Content-Type is also accepted as the audio file)
GET  /health   -> JSON

Designed for systemd socket activation (LISTEN_FDS): the process loads the model
on start, serves requests one inference at a time, and exits after IDLE_TIMEOUT_SEC
without requests so the memory is returned to the OS. systemd keeps the listening
socket open and starts the service again on the next connection.
Without LISTEN_FDS it binds HOST:PORT itself (manual debugging).
"""
import ctypes
import email.parser
import gc
import email.policy
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

MODEL_DIR = os.environ.get("MODEL_DIR", "/opt/tatar-asr/model")
IDLE_TIMEOUT_SEC = float(os.environ.get("IDLE_TIMEOUT_SEC", "900"))
NUM_THREADS = int(os.environ.get("NUM_THREADS", "2"))
CHUNK_SEC = float(os.environ.get("CHUNK_SEC", "30"))
MIN_CHUNK_SEC = float(os.environ.get("MIN_CHUNK_SEC", "20"))
MAX_AUDIO_SEC = float(os.environ.get("MAX_AUDIO_SEC", "1800"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "9002"))
SR = 16000

# Kazakh-only letters the multilingual model sometimes emits -> Tatar equivalents
KK2TT = str.maketrans({"қ": "к", "Қ": "К", "ғ": "г", "Ғ": "Г", "ұ": "у", "Ұ": "У", "і": "е", "І": "Е"})


def trim_memory():
    """Return freed heap pages to the OS (glibc keeps them otherwise: ~300 MB after load)."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def log(*a):
    print("[tatar-asr]", *a, file=sys.stderr, flush=True)


class State:
    model = None
    vocab = None          # id -> str
    blank_ids = None
    load_sec = None
    load_error = None
    loaded = threading.Event()
    infer_lock = threading.Lock()
    activity_lock = threading.Lock()
    active = 0
    last_activity = time.monotonic()
    started = time.monotonic()
    served = 0


def load_model():
    t = time.monotonic()
    try:
        import torch
        torch.set_num_threads(NUM_THREADS)
        torch.set_num_interop_threads(1)
        model = torch.load(os.path.join(MODEL_DIR, "model_int8.pt"), weights_only=False)
        model.eval()
        with open(os.path.join(MODEL_DIR, "vocab.json"), encoding="utf-8") as f:
            v = json.load(f)
        vocab = {i: s for s, i in v.items()}
        State.blank_ids = {v.get(s) for s in ("<s>", "<pad>", "</s>", "<unk>") if s in v}
        State.vocab = vocab
        State.model = model
        trim_memory()
        State.load_sec = time.monotonic() - t
        log(f"model loaded in {State.load_sec:.1f}s")
    except Exception as e:  # keep serving /health and 503s
        State.load_error = repr(e)
        log("model load FAILED:", State.load_error)
    finally:
        State.loaded.set()


def decode_audio(data: bytes) -> np.ndarray:
    p = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-t", str(MAX_AUDIO_SEC), "-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1"],
        input=data, capture_output=True, timeout=300)
    if p.returncode != 0:
        raise ValueError("ffmpeg failed: " + p.stderr.decode(errors="replace")[-300:])
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def split_points(x: np.ndarray):
    """Split into chunks <= CHUNK_SEC, cutting at the quietest 200 ms spot
    between MIN_CHUNK_SEC and CHUNK_SEC of each window."""
    n = len(x)
    max_len, min_len = int(CHUNK_SEC * SR), int(MIN_CHUNK_SEC * SR)
    hop = SR // 50  # 20 ms frames
    spans, s = [], 0
    while n - s > max_len:
        seg = x[s + min_len: s + max_len]
        nfr = len(seg) // hop
        energy = (seg[: nfr * hop].reshape(nfr, hop) ** 2).mean(axis=1)
        smooth = np.convolve(energy, np.ones(10) / 10, mode="same")  # 200 ms
        cut = s + min_len + int(np.argmin(smooth)) * hop + hop // 2
        spans.append((s, cut))
        s = cut
    if n - s > 0:
        spans.append((s, n))
    return spans


def transcribe(x: np.ndarray) -> str:
    import torch
    pieces = []
    for a, b in split_points(x):
        seg = x[a:b]
        if len(seg) < SR // 10:  # < 100 ms
            continue
        seg = (seg - seg.mean()) / np.sqrt(seg.var() + 1e-7)  # Wav2Vec2FeatureExtractor do_normalize
        with torch.inference_mode():
            logits = State.model(torch.from_numpy(seg).unsqueeze(0)).logits[0]
        ids = torch.unique_consecutive(logits.argmax(dim=-1)).tolist()
        del logits
        text = "".join(State.vocab.get(i, "") for i in ids if i not in State.blank_ids)
        text = " ".join(text.replace("|", " ").split())
        if text:
            pieces.append(text)
    return " ".join(pieces).translate(KK2TT)


def parse_upload(content_type: str, body: bytes) -> bytes:
    if content_type.lower().startswith("multipart/form-data"):
        msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
            b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
        first = None
        for part in msg.iter_parts():
            payload = part.get_payload(decode=True)
            if part.get_param("name", header="content-disposition") == "audio_file":
                return payload
            if first is None and part.get_filename():
                first = payload
        if first is not None:
            return first
        raise ValueError("multipart field 'audio_file' not found")
    return body


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "tatar-asr/1.0"

    def log_message(self, fmt, *args):
        log(self.address_string(), fmt % args)

    def _send(self, code, body, ctype="text/plain; charset=utf-8", headers=None):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Connection", "close")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)
        self.close_connection = True

    def _begin(self):
        with State.activity_lock:
            State.active += 1
            State.last_activity = time.monotonic()

    def _end(self):
        with State.activity_lock:
            State.active -= 1
            State.last_activity = time.monotonic()

    def do_GET(self):
        self._begin()
        try:
            if self.path.split("?")[0] in ("/health", "/"):
                self._send(200, json.dumps({
                    "status": "ok" if State.model is not None else ("error" if State.load_error else "loading"),
                    "model_loaded": State.model is not None,
                    "load_sec": round(State.load_sec, 2) if State.load_sec else None,
                    "load_error": State.load_error,
                    "uptime_sec": round(time.monotonic() - State.started, 1),
                    "idle_timeout_sec": IDLE_TIMEOUT_SEC,
                    "served": State.served,
                    "pid": os.getpid(),
                }), "application/json")
            else:
                self._send(404, "not found\n")
        finally:
            self._end()

    def do_POST(self):
        self._begin()
        t0 = time.monotonic()
        try:
            if self.path.split("?")[0] != "/asr":
                return self._send(404, "not found\n")
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_UPLOAD_BYTES:
                return self._send(413 if length > 0 else 400, "bad Content-Length\n")
            body = self.rfile.read(length)
            try:
                audio = decode_audio(parse_upload(self.headers.get("Content-Type", ""), body))
            except Exception as e:
                return self._send(400, f"cannot decode audio: {e}\n")
            # wait for model (cold start) - time spent here is reported as X-Load-Wait
            t_wait = time.monotonic()
            State.loaded.wait()
            t_loaded = time.monotonic()
            if State.model is None:
                return self._send(503, f"model not loaded: {State.load_error}\n")
            dur = len(audio) / SR
            with State.infer_lock:
                t_infer = time.monotonic()
                text = transcribe(audio)
                t_done = time.monotonic()
                del audio
                trim_memory()
            State.served += 1
            log(f"asr audio={dur:.1f}s infer={t_done - t_infer:.1f}s queue={t_infer - t_loaded:.1f}s "
                f"load_wait={t_loaded - t_wait:.1f}s decode={t_wait - t0:.1f}s")
            self._send(200, text + "\n", headers={
                "X-Audio-Sec": f"{dur:.2f}",
                "X-Load-Wait": f"{t_loaded - t_wait:.2f}",
                "X-Queue-Wait": f"{t_infer - t_loaded:.2f}",
                "X-Infer-Sec": f"{t_done - t_infer:.2f}",
            })
        except Exception as e:
            log("error:", repr(e))
            try:
                self._send(500, f"error: {e}\n")
            except Exception:
                pass
        finally:
            self._end()


def idle_watch(server):
    while True:
        time.sleep(5)
        with State.activity_lock:
            idle = State.active == 0 and time.monotonic() - State.last_activity >= IDLE_TIMEOUT_SEC
        if idle and IDLE_TIMEOUT_SEC > 0:
            log(f"idle {IDLE_TIMEOUT_SEC:.0f}s -> exiting to free memory")
            server.shutdown()
            return


def main():
    if os.environ.get("LISTEN_FDS") and int(os.environ.get("LISTEN_PID", os.getpid())) == os.getpid():
        sock = socket.socket(fileno=3)
        server = ThreadingHTTPServer(sock.getsockname()[:2], Handler, bind_and_activate=False)
        server.socket.close()
        server.socket = sock
        log("socket-activated on", sock.getsockname())
    else:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
        log("listening on", (HOST, PORT))
    server.daemon_threads = False  # server_close() joins in-flight requests before exit
    threading.Thread(target=load_model, daemon=True).start()
    threading.Thread(target=idle_watch, args=(server,), daemon=True).start()
    server.serve_forever(poll_interval=0.5)
    # Do not close the inherited socket's queue: pending connections stay in
    # systemd's listening socket and trigger the next activation.
    server.server_close()  # closes only our copy of the fd; waits for request threads
    log("bye")
    os._exit(0)


if __name__ == "__main__":
    main()
