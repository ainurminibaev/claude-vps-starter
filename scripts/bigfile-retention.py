#!/usr/bin/env python3
"""Большие старые файлы: спросить владельца → удалить, если не сохранил.

  bigfile-retention.py --scan   раз в неделю: найти кандидатов, разослать владельцам
                                сообщения с кнопками «Сохранить / Удалить сейчас»
  bigfile-retention.py --tick   раз в час: применить решения, по истечении срока
                                унести в корзину, корзину старше TRASH_DAYS удалить
  bigfile-retention.py --list   показать кандидатов, ничего не делая

Кандидат: файл больше MIN_SIZE, не менялся MIN_AGE_DAYS, не кэш/venv/transcript,
не веса модели, не открыт ни одним процессом. Кнопки обрабатывает патч telegram-
плагина бота (plugin-patches/telegram), решение — в bigfile-decisions.json бота.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

MIN_SIZE = 300 * 1024 * 1024
MIN_AGE_DAYS = 7
GRACE_DAYS = 3
TRASH_DAYS = 7
# /tgparser/incoming чистится своим кроном (30 дней) — сюда не включаем.
ROOTS = ["/root", "/home", "/opt", "/srv"]
SKIP_DIRS = {".cache", ".venv", "venv", "node_modules", ".claude", ".git", ".npm",
             ".local", "site-packages", "__pycache__", ".codex", ".config", "snap"}
SKIP_EXT = {".pt", ".pth", ".safetensors", ".gguf", ".onnx", ".ckpt", ".bin", ".h5", ".tflite"}
STATE = "/var/lib/bigfile-retention/state.json"
TRASH = "/var/trash"
LOG = "/var/log/bigfile-retention.log"
LANG = {"khazrat": "ar"}  # по умолчанию ru
WATCHDOG = "/root/claude-tg-watchdog.sh"


def log(msg: str) -> None:
    with open(LOG, "a") as f:
        f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + msg + "\n")


def gb(n: int) -> str:
    return f"{n / 1073741824:.1f}".replace(".", ",")


def load_state() -> dict:
    try:
        return json.load(open(STATE))
    except Exception:
        return {"files": {}}


def save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE)


def bot_users() -> list:
    out = []
    for line in open(WATCHDOG, encoding="utf-8"):
        line = line.strip().strip('"')
        if line.startswith("claude-tg") and line.count("|") == 2:
            out.append(line.split("|")[1])
    return out


def home_of(user: str) -> str:
    return "/root" if user == "root" else f"/home/{user}"


def tg_dir(user: str) -> str:
    return f"{home_of(user)}/.claude/channels/telegram"


def admin_chat() -> str:
    return json.load(open(f"{tg_dir('root')}/access.json"))["allowFrom"][0]


def bot_token(user: str) -> str:
    for line in open(f"{tg_dir(user)}/.env"):
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            return line.split("=", 1)[1].strip().strip("'\"")
    raise RuntimeError(f"no token for {user}")


def api_root(user: str) -> str:
    """Адрес Bot API бота: TELEGRAM_API_ROOT из .env (локальный сервер), иначе облако."""
    try:
        for line in open(f"{tg_dir(user)}/.env"):
            if line.startswith("TELEGRAM_API_ROOT="):
                root = line.split("=", 1)[1].strip().strip("'\"").rstrip("/")
                if root:
                    return root
    except OSError:
        pass
    return "https://api.telegram.org"


def owner_of(path: str, users: list) -> str:
    """Чей файл → какой бот спрашивает. Не из домашней папки бота — спрашивает root."""
    for u in users:
        if u != "root" and path.startswith(home_of(u) + "/"):
            return u
    return "root"


def recipients(user: str) -> list:
    """Кому писать: владельцы бота без администратора; root и общая Маруся — администратору."""
    admin = admin_chat()
    if user in ("root", "marusya"):
        return [admin]
    allow = json.load(open(f"{tg_dir(user)}/access.json")).get("allowFrom", [])
    owners = [a for a in allow if a != admin]
    return owners or [admin]


def open_files() -> set:
    res = set()
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        fd = f"/proc/{p}/fd"
        try:
            for f in os.listdir(fd):
                try:
                    res.add(os.readlink(f"{fd}/{f}"))
                except OSError:
                    pass
        except OSError:
            pass
    return res


def candidates() -> list:
    now = time.time()
    opened = open_files()
    found = []
    for root in ROOTS:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".trash")]
            for name in filenames:
                p = os.path.join(dirpath, name)
                if os.path.splitext(name)[1].lower() in SKIP_EXT:
                    continue
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if not os.path.isfile(p) or os.path.islink(p):
                    continue
                if st.st_size < MIN_SIZE or now - st.st_mtime < MIN_AGE_DAYS * 86400:
                    continue
                if p in opened:
                    continue
                found.append({"path": p, "size": st.st_size, "mtime": int(st.st_mtime)})
    return found


def token_for(path: str, mtime: int) -> str:
    return hashlib.sha1(f"{path}|{mtime}".encode()).hexdigest()[:10]


def send(user: str, chat: str, text: str, keyboard: list) -> None:
    body = {"chat_id": chat, "text": text}
    if keyboard:
        body["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    args = ["curl", "-s", "--max-time", "20", f"{api_root(user)}/bot{bot_token(user)}/sendMessage"]
    for k, v in body.items():
        args += ["--data-urlencode", f"{k}={v}"]
    r = subprocess.run(args, capture_output=True, text=True)
    if '"ok":true' not in r.stdout:
        log(f"send FAIL {user}→{chat}: {r.stdout[:200]}")


def display(path: str, user: str) -> str:
    h = home_of(user) + "/"
    return path[len(h):] if path.startswith(h) else path


def message(user: str, items: list, deadline: float) -> tuple:
    ar = LANG.get(user) == "ar"
    date = datetime.fromtimestamp(deadline).strftime("%d.%m")
    lines = []
    keyboard = []
    total = 0
    for i, it in enumerate(items, 1):
        total += it["size"]
        since = datetime.fromtimestamp(it["mtime"]).strftime("%d.%m")
        unit = "GB" if ar else "ГБ"
        lines.append(f"{i}. {display(it['path'], user)} — {gb(it['size'])} {unit}" + (f", с {since}" if not ar else ""))
        keyboard.append([
            {"text": f"💾 {i} " + ("احفظ" if ar else "Сохранить"), "callback_data": f"bf:keep:{it['token']}"},
            {"text": f"🗑 {i} " + ("احذف الآن" if ar else "Удалить сейчас"), "callback_data": f"bf:del:{it['token']}"},
        ])
    if ar:
        text = ("🗂 ملفات كبيرة قديمة على الخادم\n\n"
                "هذه الملفات لم تُستخدم منذ أكثر من أسبوع وتشغل مساحة. "
                f"إن لم تضغط «احفظ» قبل {date} سأحذفها (ويمكن استرجاعها خلال 7 أيام بعد ذلك).\n\n"
                + "\n".join(lines) + f"\n\nالمجموع: {gb(total)} غيغابايت")
    else:
        text = ("🗂 Большие старые файлы на сервере\n\n"
                "Эти файлы больше недели не менялись и занимают место. "
                f"Если не нажмёте «Сохранить» до {date}, удалю (ещё {TRASH_DAYS} дней их можно будет вернуть).\n\n"
                + "\n".join(lines) + f"\n\nВсего: {gb(total)} ГБ")
    return text, keyboard


def decisions(user: str) -> dict:
    try:
        return json.load(open(f"{tg_dir(user)}/bigfile-decisions.json"))
    except Exception:
        return {}


def to_trash(path: str, user: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d")
    dest = os.path.join(TRASH, user, stamp, path.lstrip("/"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(path, dest)
    return dest


def scan() -> None:
    st = load_state()
    files = st["files"]
    users = bot_users()
    by_user = {}
    deadline = time.time() + GRACE_DAYS * 86400
    for c in candidates():
        tok = token_for(c["path"], c["mtime"])
        rec = files.get(tok)
        if rec:  # уже спрашивали (ждём ответа, решено «сохранить» и т.п.)
            continue
        user = owner_of(c["path"], users)
        files[tok] = {**c, "token": tok, "user": user, "status": "asked",
                      "asked_at": time.time(), "deadline": deadline}
        by_user.setdefault(user, []).append(files[tok])
    for user, items in by_user.items():
        for chunk in (items[i:i + 20] for i in range(0, len(items), 20)):
            text, kb = message(user, chunk, deadline)
            for chat in recipients(user):
                send(user, chat, text, kb)
        log(f"asked {user}: {len(items)} files, {gb(sum(i['size'] for i in items))} GB")
    save_state(st)


def tick() -> None:
    st = load_state()
    files = st["files"]
    now = time.time()
    report = []
    for tok, rec in list(files.items()):
        if rec["status"] != "asked":
            continue
        path = rec["path"]
        if not os.path.exists(path):
            rec["status"] = "gone"
            continue
        if int(os.stat(path).st_mtime) != rec["mtime"]:
            rec["status"] = "changed"  # файл изменили — в следующий скан спросим заново
            continue
        dec = decisions(rec["user"]).get(tok, {}).get("decision")
        if dec == "keep":
            rec["status"] = "kept"
            log(f"keep {rec['user']} {path}")
        elif dec == "del":
            os.remove(path)
            rec["status"] = "deleted"
            report.append(f"🗑 {rec['user']}: {display(path, rec['user'])} — {gb(rec['size'])} ГБ (удалено по кнопке)")
            log(f"delete-now {rec['user']} {path}")
        elif now >= rec["deadline"]:
            rec["trash"] = to_trash(path, rec["user"])
            rec["status"] = "trashed"
            rec["trashed_at"] = now
            report.append(f"🧺 {rec['user']}: {display(path, rec['user'])} — {gb(rec['size'])} ГБ (в корзину, без ответа)")
            log(f"trash {rec['user']} {path} -> {rec['trash']}")
    for tok, rec in files.items():
        if rec["status"] == "trashed" and now - rec.get("trashed_at", now) >= TRASH_DAYS * 86400:
            try:
                os.remove(rec["trash"])
            except FileNotFoundError:
                pass
            rec["status"] = "purged"
            report.append(f"✖️ {rec['user']}: {display(rec['path'], rec['user'])} — {gb(rec['size'])} ГБ (корзина очищена)")
            log(f"purge {rec['trash']}")
    # changed/gone — забываем, чтобы при следующем скане спросить заново
    st["files"] = {k: v for k, v in files.items() if v["status"] not in ("changed", "gone")}
    save_state(st)
    if report:
        free = shutil.disk_usage("/").free
        send("root", admin_chat(), "Большие старые файлы:\n\n" + "\n".join(report) + f"\n\nСвободно на диске: {gb(free)} ГБ", [])


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--list"
    if mode == "--scan":
        scan()
    elif mode == "--tick":
        tick()
    else:
        users = bot_users()
        for c in candidates():
            print(f"{owner_of(c['path'], users):<18} {gb(c['size']):>6} ГБ  "
                  f"{datetime.fromtimestamp(c['mtime']):%Y-%m-%d}  {c['path']}")


if __name__ == "__main__":
    main()
