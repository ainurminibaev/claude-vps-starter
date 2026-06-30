#!/usr/bin/env python3
"""
auth_bot.py — отдельный TG бот для управления OAuth-логином root claude-tg.

Архитектура:
- Свой собственный TG-бот (свой токен) → 0 конфликтов с основным MCP-плагином
- Long-polling getUpdates (свой токен)
- Каждые 60 сек проверяет /root/.claude/.credentials.json:
  - refreshToken есть → молчит
  - refreshToken пуст → запускает /login в pane claude-tg, шлёт URL Айнуру
- Принимает от Айнура:
  - /start            → запоминает chat_id
  - /login            → ручной запуск flow
  - /status           → текущий refreshToken status + last expires
  - <OAuth-код>       → вставляет в pane, проверяет Login successful, шлёт ✅
- При expired code → автоматически перегенерирует URL «🔁 держи новую»
- Cooldown 1 алерт/час чтобы не спамить
"""
import json, os, sys, re, time, subprocess, threading, traceback
import urllib.request, urllib.parse, urllib.error

TOKEN = os.environ.get("AUTH_BOT_TOKEN", "").strip()
OWNER_CHAT_ID = int(os.environ.get("OWNER_CHAT_ID", "105839411"))
SESSION = os.environ.get("TMUX_SESSION", "claude-tg")
CREDS = os.environ.get("CREDS_PATH", "/root/.claude/.credentials.json")
STATE_DIR = "/var/lib/auth-bot"
LOG = "/var/log/auth-bot.log"
CHECK_INTERVAL = 60        # сек между авто-проверками credentials
ALERT_COOLDOWN = 3600      # 1 час между алертами «снова broken»
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{30,}#[A-Za-z0-9_-]{20,}$")

os.makedirs(STATE_DIR, exist_ok=True)
API = f"https://api.telegram.org/bot{TOKEN}"


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}\n")
    except Exception:
        pass
    print(msg, flush=True)


def http(url, data=None, timeout=35):
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"ok": False, "error_code": e.code, "description": str(e)}
    except Exception as e:
        return {"ok": False, "description": str(e)}


def send(chat_id, text):
    r = http(f"{API}/sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    })
    if not r.get("ok"):
        log(f"send fail: {r}")
    return r


def tmux(*args):
    return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=10)


def capture_pane(lines=80):
    r = tmux("capture-pane", "-t", SESSION, "-p", "-S", f"-{lines}")
    return r.stdout if r.returncode == 0 else ""


def get_refresh_len():
    try:
        d = json.load(open(CREDS))
        return len(str(d.get("claudeAiOauth", {}).get("refreshToken", "")))
    except Exception:
        return 0


def get_expires_h():
    try:
        d = json.load(open(CREDS))
        ex = d.get("claudeAiOauth", {}).get("expiresAt", 0)
        return (ex / 1000 - time.time()) / 3600
    except Exception:
        return 0


def start_login_and_get_url():
    """Запускает /login в pane и возвращает URL. None если не получилось."""
    tmux("send-keys", "-t", SESSION, "Escape")
    time.sleep(1)
    tmux("send-keys", "-t", SESSION, "Escape")
    time.sleep(1)
    tmux("send-keys", "-t", SESSION, "-l", "/login")
    time.sleep(0.3)
    tmux("send-keys", "-t", SESSION, "Enter")
    time.sleep(3)
    tmux("send-keys", "-t", SESSION, "Enter")  # option 1
    time.sleep(5)
    pane = capture_pane(50).replace("\n", "")
    m = re.search(r"https://claude\.com/cai/oauth/authorize\?[^\s]+", pane)
    return m.group(0) if m else None


def submit_code(code):
    """Вставляет код в pane. Returns True если Login successful детектирован."""
    tmux("send-keys", "-t", SESSION, "-l", code)
    time.sleep(0.5)
    tmux("send-keys", "-t", SESSION, "Enter")
    time.sleep(10)
    pane = capture_pane(30)
    if "Login successful" in pane or "Logged in as" in pane:
        tmux("send-keys", "-t", SESSION, "Enter")  # close modal
        time.sleep(2)
        return get_refresh_len() > 30
    return False


def nudge_claude():
    """Пнуть claude обработать накопившиеся сообщения."""
    tmux("send-keys", "-t", SESSION, "-l", "Перелогинились автоматически. Проверь TG inbox.")
    time.sleep(0.3)
    tmux("send-keys", "-t", SESSION, "Enter")


# --- monitor thread: проверяет credentials каждые 60 сек ---
monitor_state = {
    "last_alert": 0,
    "pending_code": None,        # ждём код от пользователя
    "alert_in_progress": False,  # уже шлём алерт сейчас
}


def trigger_login_flow(reason="auto", first_attempt=True):
    """
    Запускает /login, шлёт URL Айнуру.
    Устанавливает pending_code=True чтобы команда-handler знал что следующий код-сообщение
    — это OAuth-ответ.
    """
    monitor_state["alert_in_progress"] = True
    try:
        url = start_login_and_get_url()
        if not url:
            send(OWNER_CHAT_ID, f"⚠️ auth-bot ({reason}): не получилось достать URL из pane. Зайди руками: ssh, tmux attach -t {SESSION}, /login")
            monitor_state["alert_in_progress"] = False
            return False

        monitor_state["pending_code"] = True
        if first_attempt:
            text = f"🔐 root credentials.json протух ({reason}). Перелогинься:\n\n{url}\n\nПод praim199524@gmail.com (Max). Пришли код сюда — я вставлю автоматически."
        else:
            text = f"🔁 Старая ссылка протухла. Держи свежую:\n\n{url}\n\nКод живёт 10 минут."
        send(OWNER_CHAT_ID, text)
        log(f"URL отправлен ({reason}, first_attempt={first_attempt})")
        monitor_state["alert_in_progress"] = False
        return True
    except Exception as e:
        log(f"trigger_login_flow error: {e}\n{traceback.format_exc()}")
        monitor_state["alert_in_progress"] = False
        return False


def monitor_loop():
    log("monitor thread started")
    while True:
        try:
            rl = get_refresh_len()
            if rl > 30:
                # creds OK → reset cooldown
                monitor_state["last_alert"] = 0
            else:
                # creds broken
                now = time.time()
                if monitor_state["alert_in_progress"]:
                    pass  # сейчас уже шлём URL, не дублируем
                elif now - monitor_state["last_alert"] >= ALERT_COOLDOWN:
                    log(f"creds broken (refreshToken={rl}) → trigger login flow")
                    if trigger_login_flow("auto-check", first_attempt=True):
                        monitor_state["last_alert"] = now
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            log(f"monitor error: {e}")
            time.sleep(CHECK_INTERVAL)


# --- main thread: TG bot polling ---
def handle_message(msg):
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    if chat_id != OWNER_CHAT_ID:
        log(f"ignoring chat_id={chat_id} (not owner)")
        return

    if text == "/start":
        send(chat_id, "✅ Auth-бот готов. Команды:\n/login — ручной запуск перелогина\n/status — текущий статус credentials\n\nКоды OAuth (формат XXX#YYY) вставляются автоматически.")
    elif text == "/login":
        send(chat_id, "Запускаю /login flow...")
        trigger_login_flow("manual /login", first_attempt=True)
    elif text == "/status":
        rl = get_refresh_len()
        eh = get_expires_h()
        send(chat_id, f"refreshToken: {rl} chars\nexpires: {eh:+.1f}h from now\nstatus: {'✅ OK' if rl > 30 else '❌ BROKEN'}")
    elif CODE_RE.match(text):
        if not monitor_state["pending_code"]:
            send(chat_id, "⚠️ Похоже на OAuth-код, но я не ждал. Игнорирую. /login для запуска.")
            return
        ok = submit_code(text)
        monitor_state["pending_code"] = False
        if ok:
            rl = get_refresh_len()
            send(chat_id, f"✅ Авторизация успешна. refreshToken={rl} chars, +8h до expires.")
            nudge_claude()
            log("login OK")
        else:
            # код не подошёл — auto-retry
            send(chat_id, "❌ Код не принят (скорее всего expired). Генерирую новую ссылку…")
            trigger_login_flow("retry after expired", first_attempt=False)
    else:
        # игнорируем прочее
        pass


def poll_loop():
    log("poll loop started")
    offset_path = f"{STATE_DIR}/offset"
    offset = 0
    if os.path.exists(offset_path):
        try:
            offset = int(open(offset_path).read().strip())
        except Exception:
            pass
    log(f"starting offset={offset}")

    while True:
        try:
            r = http(f"{API}/getUpdates", {
                "offset": offset,
                "timeout": "30",
            }, timeout=40)
            if not r.get("ok"):
                log(f"getUpdates fail: {r}")
                time.sleep(5)
                continue
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                with open(offset_path, "w") as f:
                    f.write(str(offset))
                msg = u.get("message") or u.get("edited_message") or {}
                if msg:
                    handle_message(msg)
        except Exception as e:
            log(f"poll error: {e}")
            time.sleep(5)


def main():
    if not TOKEN:
        log("AUTH_BOT_TOKEN env not set, exiting")
        sys.exit(1)
    # стартует monitor в фоне
    threading.Thread(target=monitor_loop, daemon=True).start()
    # main thread — TG polling
    poll_loop()


if __name__ == "__main__":
    main()
