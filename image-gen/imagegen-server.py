#!/usr/bin/env python3
"""image-gen — общий сервис генерации картинок через Codex CLI на подписке ChatGPT.

Работает от системного пользователя `imagegen` (только у него ~/.codex/auth.json),
слушает unix-сокет /run/imagegen/api.sock с правами группы `imagegen-users`:
доступ к сервису = членство в группе, токен ботам недоступен.

POST /generate  JSON {"prompt": str, "size": "WxH"?, "refs": [base64]?, "timeout": int?}
  Ответ — поток NDJSON-событий (по строке на событие), чтобы клиент видел статус:
    {"event": "queued", "ahead": N}
    {"event": "attempt", "n": 1, "max": 6, "model": "..."}
    {"event": "retry", "reason": "...", "wait": 20, "next_model": "..."}
    {"event": "done", "seconds": 57, "model": "...", "attempt": 1, "image_b64": "..."}
    {"event": "error", "code": 4, "error": "..."}
GET /health  JSON {"ok": true, "queue": n, "models": [...]}

Генерации идут строго по одной (лимиты подписки). Отказы OpenAI разбираются:
перегрузка модели — повтор и переход к запасной модели; лимит подписки — стоп.
Коды ошибок совпадают с кодами клиента: 2 аргументы, 3 не залогинен, 4 лимит,
5 таймаут, 1 прочее.
"""
import base64
import json
import os
import pwd
import re
import socket
import socketserver
import struct
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler

SOCKET_PATH = os.environ.get("IMAGEGEN_SOCKET", "/run/imagegen/api.sock")
SOCKET_GROUP = os.environ.get("IMAGEGEN_GROUP", "imagegen-users")
BACKEND = os.environ.get("IMAGEGEN_BACKEND", "/usr/local/lib/imagegen/codex-image")
LOG = os.environ.get("IMAGEGEN_LOG", "/var/log/imagegen/requests.log")
# Модель-оркестратор Codex: читает промпт и вызывает инструмент image_generation.
# Саму картинку рисует инструмент — от выбора модели здесь качество не зависит.
MODELS = [m.strip() for m in os.environ.get(
    "IMAGEGEN_MODELS", "gpt-6.1-sol,gpt-6-astra,gpt-5.6-sol,gpt-5.6-terra").split(",") if m.strip()]
MAX_ATTEMPTS = int(os.environ.get("IMAGEGEN_MAX_ATTEMPTS", "6"))
MAX_BODY = 40 * 1024 * 1024
MAX_REFS = 8
DEFAULT_TIMEOUT = 900
MAX_TIMEOUT = 1800
ATTEMPT_TIMEOUT = 600

# Как понимать отказ OpenAI (текст из лога codex).
RE_LIMIT = re.compile(r"usage limit|hit your .*limit|purchase more credits|upgrade to (plus|pro)", re.I)
RE_CAPACITY = re.compile(
    r"at capacity|overloaded|try a different model|server[_ ]error|internal server error|"
    r"\b50[234]\b|bad gateway|service unavailable|temporarily unavailable|stream disconnected|"
    r"connection (reset|closed|refused)|error sending request|timed? ?out", re.I)
RE_RATE = re.compile(r"\b429\b|rate limit|too many requests", re.I)
RE_LOGIN = re.compile(r"not logged in|не залогинен|unauthorized|\b401\b|refresh token", re.I)

generate_lock = threading.Lock()
queue_size = 0
queue_guard = threading.Lock()


def log(line: str) -> None:
    try:
        with open(LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + line + "\n")
    except Exception:
        pass


def peer_user(sock: socket.socket) -> str:
    # Кто вызвал — по SO_PEERCRED, подделать нельзя. Нужен для журнала и учёта лимитов.
    try:
        creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _, uid, _ = struct.unpack("3i", creds)
        return pwd.getpwuid(uid).pw_name
    except Exception:
        return "?"


def classify(rc: int, text: str) -> str:
    """limit | login | capacity | rate | timeout | no_image | other"""
    if rc == 3 or RE_LOGIN.search(text):
        return "login"
    if RE_LIMIT.search(text):
        return "limit"
    if rc == 5:
        return "timeout"
    if RE_CAPACITY.search(text):
        return "capacity"
    if rc == 4 or RE_RATE.search(text):
        return "rate"
    if "картинка не получена" in text:
        return "no_image"
    return "other"


REASONS = {
    "capacity": "модель перегружена у OpenAI",
    "rate": "OpenAI просит сбавить частоту запросов",
    "timeout": "модель не уложилась по времени",
    "no_image": "модель не вызвала инструмент рисования",
    "other": "неизвестная ошибка",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # поток событий до закрытия соединения

    def address_string(self) -> str:
        return self.caller

    def log_message(self, fmt, *args) -> None:
        pass

    def setup(self) -> None:
        super().setup()
        self.caller = peer_user(self.connection)

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def event(self, **ev) -> None:
        try:
            self.wfile.write((json.dumps(ev, ensure_ascii=False) + "\n").encode())
            self.wfile.flush()
        except Exception:
            pass  # клиент ушёл — генерацию всё равно доводим, просто некому сказать

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(200, {"ok": True, "queue": queue_size, "models": MODELS})
        else:
            self.send_json(404, {"error": "not found", "code": 2})

    def do_POST(self) -> None:
        global queue_size
        if self.path != "/generate":
            self.send_json(404, {"error": "not found", "code": 2})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self.send_json(413, {"error": "пустой или слишком большой запрос", "code": 2})
            return
        try:
            req = json.loads(self.rfile.read(length))
            prompt = str(req["prompt"]).strip()
            size = str(req.get("size") or "").strip()
            refs = req.get("refs") or []
            budget = min(int(req.get("timeout") or DEFAULT_TIMEOUT), MAX_TIMEOUT)
        except Exception:
            self.send_json(400, {"error": "нужен JSON с полем prompt", "code": 2})
            return
        if not prompt or len(prompt) > 4000:
            self.send_json(400, {"error": "prompt пустой или длиннее 4000 символов", "code": 2})
            return
        if size and not re.fullmatch(r"\d{2,5}x\d{2,5}", size.lower()):
            self.send_json(400, {"error": "size в формате WxH", "code": 2})
            return
        if len(refs) > MAX_REFS:
            self.send_json(400, {"error": f"не больше {MAX_REFS} референсов", "code": 2})
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.end_headers()

        with queue_guard:
            ahead = queue_size
            queue_size += 1
        log(f"{self.caller} queued ahead={ahead} prompt={prompt[:120]!r} refs={len(refs)}")
        if ahead:
            self.event(event="queued", ahead=ahead)
        try:
            with generate_lock:
                self.run(prompt, size, refs, budget)
        finally:
            with queue_guard:
                queue_size -= 1

    def run(self, prompt: str, size: str, refs: list, budget: int) -> None:
        started = time.time()
        with tempfile.TemporaryDirectory(prefix="imagegen-") as work:
            ref_paths = []
            for i, r in enumerate(refs):
                try:
                    data = base64.b64decode(r, validate=True)
                except Exception:
                    self.event(event="error", code=2, error=f"референс {i + 1} — не base64")
                    return
                path = os.path.join(work, f"ref{i + 1}.img")
                with open(path, "wb") as f:
                    f.write(data)
                ref_paths.append(path)

            model_idx = 0
            same_model_retries = 0
            last = ""
            for attempt in range(1, MAX_ATTEMPTS + 1):
                left = budget - (time.time() - started)
                if left < 60:
                    break
                model = MODELS[min(model_idx, len(MODELS) - 1)]
                self.event(event="attempt", n=attempt, max=MAX_ATTEMPTS, model=model)
                rc, text, png = self.attempt(work, prompt, size, ref_paths, model, int(min(left, ATTEMPT_TIMEOUT)))
                if png:
                    secs = int(time.time() - started)
                    log(f"{self.caller} ok model={model} attempt={attempt} {len(png)}B total={secs}s")
                    self.event(event="done", seconds=secs, model=model, attempt=attempt,
                               image_b64=base64.b64encode(png).decode())
                    return

                kind = classify(rc, text)
                last = text[-800:].strip()
                log(f"{self.caller} attempt={attempt} model={model} FAIL kind={kind} rc={rc} {last[-200:]!r}")
                if kind == "login":
                    self.event(event="error", code=3, error="сервис разлогинился в ChatGPT — нужен администратор")
                    return
                if kind == "limit":
                    self.event(event="error", code=4, error="исчерпан лимит подписки ChatGPT. " + last[-300:])
                    return

                # Перегрузка/обрыв: один повтор на той же модели, потом следующая.
                # Модель не нарисовала или таймаут: сразу следующая.
                if kind in ("capacity", "rate") and same_model_retries < 1:
                    same_model_retries += 1
                    wait = 60 if kind == "rate" else 20
                else:
                    same_model_retries = 0
                    model_idx += 1
                    wait = 5
                    if model_idx >= len(MODELS):
                        model_idx = 0  # круг по цепочке, пока есть попытки и время
                next_model = MODELS[model_idx]
                if attempt < MAX_ATTEMPTS:
                    self.event(event="retry", reason=REASONS.get(kind, kind), wait=wait, next_model=next_model)
                    time.sleep(wait)

            secs = int(time.time() - started)
            code = 5 if (time.time() - started) >= budget - 60 else 1
            log(f"{self.caller} GAVE UP after {secs}s")
            self.event(event="error", code=code,
                       error=f"не получилось за {MAX_ATTEMPTS} попыток / {secs} с. Последняя ошибка: {last[-300:]}")

    def attempt(self, work, prompt, size, ref_paths, model, tmo):
        out = os.path.join(work, "result.png")
        if os.path.exists(out):
            os.unlink(out)
        cmd = [BACKEND, prompt, "-o", out, "--timeout", str(tmo), "--model", model]
        if size:
            cmd += ["--size", size]
        for pth in ref_paths:
            cmd += ["--ref", pth]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=tmo + 60)
        except subprocess.TimeoutExpired:
            return 5, "timeout", None
        if proc.returncode == 0 and os.path.exists(out):
            with open(out, "rb") as f:
                return 0, "", f.read()
        return proc.returncode, (proc.stderr or "") + (proc.stdout or ""), None


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main() -> None:
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    srv = Server(SOCKET_PATH, Handler)
    try:
        import grp
        os.chown(SOCKET_PATH, -1, grp.getgrnam(SOCKET_GROUP).gr_gid)
    except Exception as e:
        log(f"chown socket failed: {e}")
    os.chmod(SOCKET_PATH, 0o660)
    log(f"listening on {SOCKET_PATH}, models={MODELS}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
