#!/usr/bin/env python3
"""gigaam-asr: language ID + Russian ASR without torch (numpy + onnxruntime + sentencepiece).

  * LID — VoxLingua107 ECAPA (speechbrain/lang-id-voxlingua107-ecapa, Apache-2.0) exported to ONNX
    (export_models.py): 107 languages incl. ru, tt, en, ar. East-Slavic confusion is normal for it (Russian
    voice notes come out as ru/be/uk), so ru = ru+be+uk; Turkic (tt, ba, kk, ky, uz, az, tk) = tt.
  * Russian ASR — GigaAM v3_e2e_ctc (Sber, MIT) in ONNX int8: punctuation and normalisation built in.
    GigaAM takes <=25 s per pass, so audio is cut at the quietest 200 ms spot into chunks <= CHUNK_SEC.
Everything that is not Russian (tt, en, ar, ...) goes to the Omnilingual service (tatar-asr, :9002) — the
decision is made by the caller (/usr/local/bin/voice-to-text).

API:
  POST /detect-language  multipart `audio_file` -> {"language_code", "confidence", "ru", "top", "audio_sec"}
  POST /asr?language=ru  multipart `audio_file` -> text/plain, or JSON with output=json
  GET  /health
systemd socket activation, models loaded on the first request, one inference at a time, process exits after
IDLE_TIMEOUT_SEC without requests (memory goes back to the OS) — same lifecycle as tatar-asr.
"""
import ctypes
import email.parser
import email.policy
import gc
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import numpy as np

MODEL_DIR = os.environ.get("MODEL_DIR", "/opt/gigaam-asr/models")
NUM_THREADS = int(os.environ.get("NUM_THREADS", "2"))
IDLE_TIMEOUT_SEC = float(os.environ.get("IDLE_TIMEOUT_SEC", "600"))
CHUNK_SEC = float(os.environ.get("CHUNK_SEC", "22"))
MIN_CHUNK_SEC = float(os.environ.get("MIN_CHUNK_SEC", "12"))
LID_SEC = float(os.environ.get("LID_SEC", "20"))           # language from the first N seconds
MAX_AUDIO_SEC = float(os.environ.get("MAX_AUDIO_SEC", "1800"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "9003"))
SR = 16000
GROUPS = {"ru": ("ru", "be", "uk"), "tt": ("tt", "ba", "kk", "ky", "uz", "az", "tk")}


def trim_memory():
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def log(*a):
    print("[gigaam-asr]", *a, file=sys.stderr, flush=True)


# ---------- GigaAM features: torchaudio MelSpectrogram(16 kHz, n_fft=win=320, hop=160, 64 mels, htk,
# norm=None, center=False, power=2, periodic hann) + log(clamp(1e-9, 1e9)) — same numbers in numpy ----------
N_FFT, HOP, N_MELS = 320, 160, 64


def _mel_fb() -> np.ndarray:
    def hz2mel(f):
        return 2595.0 * np.log10(1.0 + f / 700.0)

    def mel2hz(m):
        return 700.0 * (10.0 ** (m / 2595.0) - 1.0)
    freqs = np.linspace(0, SR // 2, N_FFT // 2 + 1)
    f_pts = mel2hz(np.linspace(hz2mel(0.0), hz2mel(SR / 2), N_MELS + 2))
    f_diff = f_pts[1:] - f_pts[:-1]
    slopes = f_pts[None, :] - freqs[:, None]
    down = -slopes[:, :-2] / f_diff[:-1]
    up = slopes[:, 2:] / f_diff[1:]
    return np.maximum(0.0, np.minimum(down, up)).astype(np.float32)     # [161, 64]


MEL_FB = _mel_fb()
WINDOW = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)).astype(np.float32)


def gigaam_features(x: np.ndarray) -> np.ndarray:
    n = 1 + (len(x) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * WINDOW, n=N_FFT)) ** 2               # [T, 161]
    mel = spec.astype(np.float32) @ MEL_FB                                  # [T, 64]
    return np.log(np.clip(mel, 1e-9, 1e9)).T[None].astype(np.float32)       # [1, 64, T]


class State:
    lid = gigaam = sp = None
    labels: list = []
    load_sec = None
    load_error = None
    loaded = threading.Event()
    infer_lock = threading.Lock()
    activity_lock = threading.Lock()
    active = 0
    last_activity = time.monotonic()
    started = time.monotonic()
    served = 0


def load_models():
    t = time.monotonic()
    try:
        import onnxruntime as rt
        import sentencepiece
        opts = rt.SessionOptions()
        opts.intra_op_num_threads = NUM_THREADS
        opts.inter_op_num_threads = 1
        opts.execution_mode = rt.ExecutionMode.ORT_SEQUENTIAL
        opts.graph_optimization_level = rt.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.enable_cpu_mem_arena = False          # variable-length audio: the arena would only grow
        opts.log_severity_level = 3
        State.lid = rt.InferenceSession(os.path.join(MODEL_DIR, "lid.onnx"), opts, providers=["CPUExecutionProvider"])
        State.gigaam = rt.InferenceSession(os.path.join(MODEL_DIR, "gigaam.int8.onnx"), opts,
                                           providers=["CPUExecutionProvider"])
        State.sp = sentencepiece.SentencePieceProcessor(model_file=os.path.join(MODEL_DIR, "tokenizer.model"))
        with open(os.path.join(MODEL_DIR, "lid_labels.txt"), encoding="utf-8") as f:
            State.labels = [x.strip() for x in f if x.strip()]
        trim_memory()
        State.load_sec = time.monotonic() - t
        log(f"models loaded in {State.load_sec:.1f}s")
    except Exception as e:
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
    n = len(x)
    max_len, min_len = int(CHUNK_SEC * SR), int(MIN_CHUNK_SEC * SR)
    hop = SR // 50
    spans, s = [], 0
    while n - s > max_len:
        seg = x[s + min_len: s + max_len]
        nfr = len(seg) // hop
        energy = (seg[: nfr * hop].reshape(nfr, hop) ** 2).mean(axis=1)
        smooth = np.convolve(energy, np.ones(10) / 10, mode="same")
        cut = s + min_len + int(np.argmin(smooth)) * hop + hop // 2
        spans.append((s, cut))
        s = cut
    if n - s > 0:
        spans.append((s, n))
    return spans


def detect(x: np.ndarray) -> dict:
    seg = x[: int(LID_SEC * SR)]
    if len(seg) < SR // 2:
        seg = np.pad(seg, (0, SR // 2 - len(seg)))
    logp = State.lid.run(None, {"wav": seg[None].astype(np.float32)})[0][0]
    p = np.exp(logp - logp.max())
    p = p / p.sum()
    prob = {lab: float(v) for lab, v in zip(State.labels, p)}
    grouped = {g: sum(prob.get(x, 0.0) for x in members) for g, members in GROUPS.items()}
    for lab, v in prob.items():
        if not any(lab in m for m in GROUPS.values()):
            grouped[lab] = v
    lang = max(grouped, key=grouped.get)
    top = sorted(prob.items(), key=lambda kv: -kv[1])[:3]
    return {"language_code": lang, "confidence": round(grouped[lang], 4), "ru": round(grouped["ru"], 4),
            "top": [[k, round(v, 3)] for k, v in top]}


def transcribe_ru(x: np.ndarray) -> str:
    pieces = []
    blank = State.sp.get_piece_size()
    for a, b in split_points(x):
        seg = x[a:b]
        if len(seg) < N_FFT * 2:
            continue
        feats = gigaam_features(seg)
        logp, lens = State.gigaam.run(None, {"features": feats,
                                             "feature_lengths": np.array([feats.shape[-1]], dtype=np.int64)})
        ids = logp[0, : int(lens[0])].argmax(-1)
        keep = (ids != blank) & np.concatenate([[True], ids[1:] != ids[:-1]])
        text = State.sp.decode([int(i) for i in ids[keep]])
        text = " ".join(text.split())
        if text:
            pieces.append(text)
    return " ".join(pieces)


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
    server_version = "gigaam-asr/1.0"

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
            if urlsplit(self.path).path in ("/health", "/"):
                self._send(200, json.dumps({
                    "status": "ok" if State.gigaam is not None else ("error" if State.load_error else "loading"),
                    "models": ["voxlingua107-ecapa (onnx)", "gigaam-v3-e2e-ctc (onnx int8)"],
                    "load_sec": round(State.load_sec, 2) if State.load_sec else None,
                    "load_error": State.load_error, "uptime_sec": round(time.monotonic() - State.started, 1),
                    "idle_timeout_sec": IDLE_TIMEOUT_SEC, "served": State.served, "pid": os.getpid(),
                }), "application/json")
            else:
                self._send(404, "not found\n")
        finally:
            self._end()

    def do_POST(self):
        self._begin()
        t0 = time.monotonic()
        try:
            url = urlsplit(self.path)
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            if url.path not in ("/asr", "/detect-language"):
                return self._send(404, "not found\n")
            if url.path == "/asr" and q.get("language", "ru") != "ru":
                return self._send(400, "only language=ru here; other languages -> Omnilingual (:9002)\n")
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_UPLOAD_BYTES:
                return self._send(413 if length > 0 else 400, "bad Content-Length\n")
            body = self.rfile.read(length)
            try:
                audio = decode_audio(parse_upload(self.headers.get("Content-Type", ""), body))
            except Exception as e:
                return self._send(400, f"cannot decode audio: {e}\n")
            del body
            t_wait = time.monotonic()
            State.loaded.wait()
            t_loaded = time.monotonic()
            if State.gigaam is None:
                return self._send(503, f"models not loaded: {State.load_error}\n")
            dur = len(audio) / SR
            with State.infer_lock:
                t_infer = time.monotonic()
                out = detect(audio) if url.path == "/detect-language" else {"text": transcribe_ru(audio)}
                t_done = time.monotonic()
                del audio
                trim_memory()
            State.served += 1
            log(f"{url.path} audio={dur:.1f}s infer={t_done - t_infer:.2f}s queue={t_infer - t_loaded:.1f}s "
                f"load_wait={t_loaded - t_wait:.1f}s decode={t_wait - t0:.1f}s "
                + (f"lang={out['language_code']} conf={out['confidence']:.2f} ru={out['ru']:.2f}"
                   if "language_code" in out else ""))
            headers = {"X-Audio-Sec": f"{dur:.2f}", "X-Load-Wait": f"{t_loaded - t_wait:.2f}",
                       "X-Queue-Wait": f"{t_infer - t_loaded:.2f}", "X-Infer-Sec": f"{t_done - t_infer:.2f}"}
            out.update(audio_sec=round(dur, 2), infer_sec=round(t_done - t_infer, 2),
                       load_wait=round(t_loaded - t_wait, 2), model="gigaam-v3-e2e-ctc-int8")
            if url.path == "/detect-language" or q.get("output") == "json":
                return self._send(200, json.dumps(out, ensure_ascii=False), "application/json", headers)
            self._send(200, out["text"] + "\n", headers=headers)
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
    server.daemon_threads = False
    threading.Thread(target=load_models, daemon=True).start()
    threading.Thread(target=idle_watch, args=(server,), daemon=True).start()
    server.serve_forever(poll_interval=0.5)
    server.server_close()
    log("bye")
    os._exit(0)


if __name__ == "__main__":
    main()
