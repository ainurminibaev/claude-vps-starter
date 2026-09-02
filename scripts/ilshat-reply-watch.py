#!/usr/bin/env python3
# Следит за ботом ilshat: есть ли входящее в Telegram, на которое не ушёл reply.
# Запускается кроном раз в час, шлёт уведомление Айнуру.
import glob, json, os, re, subprocess, time

BOT_USER = "ilshat"
HOME = "/home/" + BOT_USER
NOTIFY_CHAT = "105839411"          # Айнур
SESSION = "claude-tg-" + BOT_USER
ENV_FILE = HOME + "/.claude/channels/telegram/.env"
STATE = "/var/lib/ilshat-reply-watch.state"
LOG = "/var/log/ilshat-reply-watch.log"
MIN_AGE_MIN = 30                   # молчание короче — ещё не повод будить
TAIL_BYTES = 3_000_000


def log(m):
    try:
        with open(LOG, "a") as f:
            f.write("[" + time.strftime("%Y-%m-%dT%H:%M:%S") + "] " + str(m) + "\n")
    except Exception:
        pass


def newest_transcript():
    files = glob.glob(HOME + "/.claude/projects/*/*.jsonl")
    return max(files, key=os.path.getmtime) if files else None


def scan(path):
    last_in = last_out = None
    in_text = ""
    size = os.path.getsize(path)
    with open(path, errors="ignore") as f:
        if size > TAIL_BYTES:
            f.seek(size - TAIL_BYTES)
            f.readline()
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = d.get("timestamp", "")
            msg = d.get("message") or {}
            c = msg.get("content")
            if d.get("type") == "user" and isinstance(c, str) and "plugin:telegram" in c:
                last_in = ts
                m = re.search(r">\s*(.{0,120})", c, re.S)
                in_text = (m.group(1).strip() if m else "")[:120]
            if isinstance(c, list):
                for it in c:
                    if not isinstance(it, dict):
                        continue
                    name = it.get("name", "")
                    if it.get("type") == "tool_use" and "telegram" in name \
                       and name.endswith(("reply", "edit_message")):
                        last_out = ts
    return last_in, last_out, in_text


def busy():
    # Идёт ход — ответ ещё может уйти, тревожить рано.
    try:
        pane = subprocess.run(
            ["sudo", "-u", BOT_USER, "tmux", "capture-pane", "-p", "-t", SESSION],
            capture_output=True, text=True, timeout=20).stdout
        return "esc to interrupt" in pane
    except Exception:
        return False


def read_token():
    try:
        for line in open(ENV_FILE):
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                return line.strip().split("=", 1)[1].strip().strip("\"").strip("'")
    except Exception as e:
        log("env fail: " + str(e))
    return None


def send(text):
    token = read_token()
    if not token:
        log("no token")
        return
    r = subprocess.run([
        "curl", "-s", "--max-time", "15",
        "https://api.telegram.org/bot" + token + "/sendMessage",
        "-d", "chat_id=" + NOTIFY_CHAT,
        "--data-urlencode", "text=" + text,
    ], capture_output=True, text=True, timeout=25)
    log("notify ok=" + str('"ok":true' in r.stdout))


def main():
    t = newest_transcript()
    if not t:
        log("no transcript")
        return

    last_in, last_out, in_text = scan(t)
    if not last_in:
        log("нет входящих в хвосте")
        return
    if last_out and last_out >= last_in:
        log("OK: всё отвечено")
        return
    if busy():
        log("бот в работе, пропускаю")
        return

    age = (time.time() - time.mktime(time.strptime(last_in[:19], "%Y-%m-%dT%H:%M:%S"))) / 60
    if age < MIN_AGE_MIN:
        log("молчит " + str(int(age)) + " мин — рано")
        return

    prev = ""
    try:
        prev = open(STATE).read().strip()
    except Exception:
        pass
    if prev == last_in:
        log("уже уведомлял про это сообщение")
        return

    text = (
        "Бот Ильшата не ответил на сообщение\n\n"
        "Пришло: " + last_in[11:16] + " UTC (" + str(int(age)) + " мин назад)\n"
        "Текст: " + in_text + "\n\n"
        "Последний ответ бота: " + (last_out[:16].replace("T", " ") if last_out else "нет в хвосте") + "\n"
        "Сессия жива, ход завершён — ответ, скорее всего, остался в консоли."
    )
    send(text)
    try:
        with open(STATE, "w") as f:
            f.write(last_in)
    except Exception:
        pass
    log("УВЕДОМЛЕНИЕ: не отвечено с " + last_in)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("unhandled: " + type(e).__name__ + ": " + str(e))
