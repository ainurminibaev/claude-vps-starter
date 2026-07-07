#!/usr/bin/env python3
"""
auth_bot.py — отдельный TG-бот для управления OAuth-логином всех 9 claude-tg ботов на VPS.

Архитектура:
- Собственный TG-бот со своим токеном (0 конфликтов с polling основных MCP-плагинов)
- Long-polling getUpdates
- В фоне каждые 60 сек проверяет credentials.json КАЖДОГО из 9 ботов
- Если refreshToken=0 → шлёт уведомление владельцу ("⚠️ <name> протух, пришли /login <name>")
- Cooldown 1 час на каждый бот отдельно (не спамим)

Команды от владельца:
- /start                     — приветствие
- /status                    — сводка по всем 9
- /status <name>             — конкретный
- /login <name>              — генерит свежий URL (запускает /login в pane нужного pane)
- <OAuth-код XXX#YYY>        — вставляется в pane того бота, для которого был последний /login
- При expired code → авто-перегенерирует URL

Работает под root: для чужих tmux-сессий использует `sudo -u <user> tmux ...`.
"""
import json, os, sys, re, time, subprocess, threading, traceback
import urllib.request, urllib.parse, urllib.error

TOKEN = os.environ.get("AUTH_BOT_TOKEN", "").strip()
OWNER_CHAT_ID = int(os.environ.get("OWNER_CHAT_ID", "105839411"))
STATE_DIR = "/var/lib/auth-bot"
LOG = "/var/log/auth-bot.log"
CHECK_INTERVAL = 60
ALERT_COOLDOWN = 3600
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{30,}#[A-Za-z0-9_-]{20,}$")

# Реестр всех 9 ботов. Ключ — короткий alias, используется в командах.
BOTS = {
    "root":    {"user": "root",              "session": "claude-tg",                    "creds": "/root/.claude/.credentials.json"},
    "wife":    {"user": "wife",              "session": "claude-tg-wife",               "creds": "/home/wife/.claude/.credentials.json"},
    "rafka":   {"user": "rafka",             "session": "claude-tg-rafka",              "creds": "/home/rafka/.claude/.credentials.json"},
    "bulatov": {"user": "bulatov",           "session": "claude-tg-bulatov",            "creds": "/home/bulatov/.claude/.credentials.json"},
    "alfiya":  {"user": "alfiya-mama-rafka", "session": "claude-tg-alfiya-mama-rafka",  "creds": "/home/alfiya-mama-rafka/.claude/.credentials.json"},
    "rishat":  {"user": "rishat-rafka-papa", "session": "claude-tg-rishat-rafka-papa",  "creds": "/home/rishat-rafka-papa/.claude/.credentials.json"},
    "khazrat": {"user": "khazrat",           "session": "claude-tg-khazrat",            "creds": "/home/khazrat/.claude/.credentials.json"},
    "niyaz":   {"user": "niyaz",             "session": "claude-tg-niyaz",              "creds": "/home/niyaz/.claude/.credentials.json"},
    "diana":   {"user": "diana",             "session": "claude-tg-diana",              "creds": "/home/diana/.claude/.credentials.json"},
    "ilshat":  {"user": "ilshat",            "session": "claude-tg-ilshat",             "creds": "/home/ilshat/.claude/.credentials.json"},
}

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


def tmux(bot_name, *args):
    """
    Обёртка над tmux. Для root — прямой вызов, для остальных — через sudo -u <user>
    (потому что tmux-сервер у каждого пользователя свой).
    """
    user = BOTS[bot_name]["user"]
    cmd = ["tmux", *args] if user == "root" else ["sudo", "-u", user, "tmux", *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15)


def capture_pane(bot_name, lines=80):
    session = BOTS[bot_name]["session"]
    r = tmux(bot_name, "capture-pane", "-t", session, "-p", "-S", f"-{lines}")
    return r.stdout if r.returncode == 0 else ""


def send_keys(bot_name, *args):
    session = BOTS[bot_name]["session"]
    return tmux(bot_name, "send-keys", "-t", session, *args)


def get_refresh_len(bot_name):
    try:
        d = json.load(open(BOTS[bot_name]["creds"]))
        return len(str(d.get("claudeAiOauth", {}).get("refreshToken", "")))
    except Exception:
        return 0


def get_expires_h(bot_name):
    try:
        d = json.load(open(BOTS[bot_name]["creds"]))
        ex = d.get("claudeAiOauth", {}).get("expiresAt", 0)
        return (ex / 1000 - time.time()) / 3600
    except Exception:
        return 0


def start_login_and_get_url(bot_name):
    """Запускает /login в pane бота, возвращает URL или None."""
    send_keys(bot_name, "Escape")
    time.sleep(1)
    send_keys(bot_name, "Escape")
    time.sleep(1)
    send_keys(bot_name, "-l", "/login")
    time.sleep(0.3)
    send_keys(bot_name, "Enter")
    time.sleep(3)
    send_keys(bot_name, "Enter")  # опция 1
    time.sleep(5)
    pane = capture_pane(bot_name, 50).replace("\n", "")
    m = re.search(r"https://claude\.com/cai/oauth/authorize\?[^\s]+", pane)
    return m.group(0) if m else None


def submit_code(bot_name, code):
    """Вставляет код в pane бота. True если Login successful."""
    send_keys(bot_name, "-l", code)
    time.sleep(0.5)
    send_keys(bot_name, "Enter")
    time.sleep(10)
    pane = capture_pane(bot_name, 30)
    if "Login successful" in pane or "Logged in as" in pane:
        send_keys(bot_name, "Enter")  # закрыть модалку
        time.sleep(2)
        return get_refresh_len(bot_name) > 30
    return False


def nudge_claude(bot_name, hours_offline=None):
    """Пнуть claude в этом pane обработать накопившиеся сообщения."""
    if hours_offline and hours_offline > 24:
        text = f"Перелогинились после {hours_offline:.0f}ч простоя. Проверь TG inbox — самое важное первым, не всё сразу."
    else:
        text = "Перелогинились. Проверь TG inbox и ответь на пропущенное."
    send_keys(bot_name, "-l", text)
    time.sleep(0.3)
    send_keys(bot_name, "Enter")


# --- state ---
last_alert = {name: 0.0 for name in BOTS}
pending_code_for = {"name": None}  # какой бот ждёт свой OAuth-код


# --- login flow ---
def issue_fresh_url(bot_name, retry=False):
    """Генерит свежий URL для конкретного бота и шлёт владельцу."""
    try:
        url = start_login_and_get_url(bot_name)
        if not url:
            send(OWNER_CHAT_ID, f"⚠️ {bot_name}: не получилось достать URL из pane. Зайди руками: sudo -u {BOTS[bot_name]['user']} tmux attach -t {BOTS[bot_name]['session']}, /login")
            return False
        pending_code_for["name"] = bot_name
        if retry:
            text = f"🔁 {bot_name}: старая ссылка протухла. Держи свежую (10 мин):\n\n{url}"
        else:
            text = f"🔐 Перелогинь **{bot_name}** (10 минут):\n\n{url}\n\nПришли код сюда — я вставлю."
        send(OWNER_CHAT_ID, text)
        log(f"{bot_name}: URL отправлен (retry={retry})")
        return True
    except Exception as e:
        log(f"{bot_name}: issue_fresh_url error: {e}\n{traceback.format_exc()}")
        return False


# --- monitor thread ---
def monitor_loop():
    log("monitor thread started")
    while True:
        try:
            now = time.time()
            for name in BOTS:
                rl = get_refresh_len(name)
                if rl > 30:
                    last_alert[name] = 0
                else:
                    if now - last_alert[name] >= ALERT_COOLDOWN:
                        eh = get_expires_h(name)
                        hours_off = max(0, -eh)
                        log(f"{name}: creds broken (refreshToken={rl}, offline {hours_off:.1f}h) → notify owner")
                        send(OWNER_CHAT_ID,
                             f"⚠️ {name} credentials.json протух (refreshToken=0, offline {hours_off:.0f}h).\n"
                             f"Пришли `/login {name}` — сгенерю свежую ссылку.")
                        last_alert[name] = now
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            log(f"monitor error: {e}\n{traceback.format_exc()}")
            time.sleep(CHECK_INTERVAL)


# --- status ---
def status_all():
    lines = ["📊 Статус всех 9 ботов:\n"]
    for name in BOTS:
        rl = get_refresh_len(name)
        eh = get_expires_h(name)
        if rl > 30:
            lines.append(f"✅ {name}: OK ({eh:+.1f}h)")
        else:
            hours_off = max(0, -eh)
            lines.append(f"❌ {name}: BROKEN (offline {hours_off:.0f}h)")
    return "\n".join(lines)


def status_one(name):
    if name not in BOTS:
        return f"❌ Неизвестный бот: {name}\nДоступны: {', '.join(BOTS.keys())}"
    rl = get_refresh_len(name)
    eh = get_expires_h(name)
    ok = rl > 30
    return f"{'✅' if ok else '❌'} {name}\nrefreshToken: {rl} chars\nexpires: {eh:+.1f}h\nstatus: {'OK' if ok else 'BROKEN'}"


# --- TG handlers ---
def handle_message(msg):
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    if chat_id != OWNER_CHAT_ID:
        log(f"ignoring chat_id={chat_id} (not owner)")
        return

    parts = text.split(maxsplit=1)
    cmd = parts[0] if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/start":
        botlist = ", ".join(BOTS.keys())
        send(chat_id,
             "✅ Auth-бот готов. Команды:\n"
             "/status — сводка по всем 9\n"
             "/status <name> — конкретный\n"
             "/login <name> — генерит свежую ссылку\n\n"
             f"Боты: {botlist}\n\n"
             "OAuth-коды (XXX#YYY) вставляются автоматически в pane того бота,\n"
             "для которого был последний /login.")

    elif cmd == "/status":
        if arg:
            send(chat_id, status_one(arg))
        else:
            send(chat_id, status_all())

    elif cmd == "/login":
        if not arg or arg not in BOTS:
            send(chat_id, f"❌ Укажи бот: /login <name>\nДоступны: {', '.join(BOTS.keys())}")
            return
        send(chat_id, f"Генерирую ссылку для {arg}...")
        issue_fresh_url(arg, retry=False)

    elif CODE_RE.match(text):
        target = pending_code_for["name"]
        if not target:
            send(chat_id, "⚠️ Похоже на OAuth-код, но я не ждал. Начни с /login <name>.")
            return
        send(chat_id, f"Вставляю в {target}…")
        ok = submit_code(target, text)
        pending_code_for["name"] = None
        if ok:
            rl = get_refresh_len(target)
            eh_before = get_expires_h(target)
            offline = max(0, -eh_before) if eh_before < 0 else None
            hours_off_msg = f" (был offline {offline:.0f}ч)" if offline else ""
            send(chat_id, f"✅ {target}: авторизация успешна. refreshToken={rl}, +8h до expires{hours_off_msg}.")
            nudge_claude(target, hours_offline=offline)
            log(f"{target}: login OK")
        else:
            send(chat_id, f"❌ {target}: код не принят (expired?). Генерирую новую ссылку…")
            issue_fresh_url(target, retry=True)

    else:
        pass


# --- poll loop ---
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
            r = http(f"{API}/getUpdates", {"offset": offset, "timeout": "30"}, timeout=40)
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
    threading.Thread(target=monitor_loop, daemon=True).start()
    poll_loop()


if __name__ == "__main__":
    main()
