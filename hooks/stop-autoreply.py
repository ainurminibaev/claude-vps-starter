#!/usr/bin/env python3
"""
Stop hook for claude-tg — v3.

Изменения от v2:
- Long-message chunking: TG лимит 4096 chars/msg, бьём text >3800 на части
  по abзацам -> строкам -> жёстко по символам. Отправляем последовательно с паузой 0.5с.
- Универсальный: пути вычисляются из $HOME/$USER.
"""
import json, os, sys, re, subprocess, time

HOME = os.path.expanduser("~")
USER = os.environ.get("USER") or os.path.basename(HOME)
LOG = f"/var/log/stop-autoreply-{USER}.log" if USER != "root" else "/var/log/stop-autoreply.log"
MARKER_DIR = "/tmp/stop-autoreply-markers"
ENV_FILE = f"{HOME}/.claude/channels/telegram/.env"
TG_LIMIT = 3800
TG_TOOLS_WITH_TEXT = {
    "mcp__plugin_telegram_telegram__reply",
    "mcp__plugin_telegram_telegram__edit_message",
    "mcp__plugin_telegram_telegram__send_message",
}


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def normalize(s):
    s = re.sub(r"\\([_*\[\]()~`>#+\-=|{}.!])", r"\1", s or "")
    return re.sub(r"\s+", " ", s.strip())


def text_already_sent(block_text, sent_args):
    nb = normalize(block_text)
    if not nb:
        return True
    if len(nb) < 8:
        return any(normalize(a) == nb for a in sent_args)
    for a in sent_args:
        na = normalize(a)
        if nb in na or na in nb:
            return True
    return False


def get_text_blocks(msg):
    out = []
    for c in msg.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            t = (c.get("text") or "").strip()
            if t:
                out.append(t)
    return out


def get_tool_text_args(msg):
    out = []
    for c in msg.get("content") or []:
        if not isinstance(c, dict) or c.get("type") != "tool_use":
            continue
        if c.get("name") not in TG_TOOLS_WITH_TEXT:
            continue
        inp = c.get("input") or {}
        t = inp.get("text") or inp.get("message") or inp.get("caption")
        if t:
            out.append(t)
    return out


def chunk_text(text, limit=TG_LIMIT):
    """Бьёт text на части <=limit: сначала по абзацам, потом строкам, потом жёстко."""
    if len(text) <= limit:
        return [text]

    out = []

    def hard_cut(s):
        for i in range(0, len(s), limit):
            out.append(s[i:i + limit])

    def add_by_lines(para):
        cur = ""
        for line in para.split("\n"):
            cand = cur + ("\n" if cur else "") + line
            if len(cand) <= limit:
                cur = cand
            else:
                if cur:
                    out.append(cur)
                    cur = ""
                if len(line) <= limit:
                    cur = line
                else:
                    hard_cut(line)
        if cur:
            out.append(cur)

    cur = ""
    for para in text.split("\n\n"):
        cand = cur + ("\n\n" if cur else "") + para
        if len(cand) <= limit:
            cur = cand
        else:
            if cur:
                out.append(cur)
                cur = ""
            if len(para) <= limit:
                cur = para
            else:
                add_by_lines(para)
    if cur:
        out.append(cur)
    return out


def send_message(token, chat_id, text):
    res = subprocess.run([
        "curl", "-s", "--max-time", "10",
        f"https://api.telegram.org/bot{token}/sendMessage",
        "-d", f"chat_id={chat_id}",
        "--data-urlencode", f"text={text}",
    ], capture_output=True, text=True, timeout=15)
    try:
        return json.loads(res.stdout)
    except Exception:
        return {"ok": False, "raw": res.stdout[:200]}


def main():
    try:
        event = json.load(sys.stdin)
    except Exception as e:
        log(f"stdin parse fail: {e}")
        return

    transcript = event.get("transcript_path")
    session_id = event.get("session_id", "unknown")
    if not transcript or not os.path.exists(transcript):
        log(f"no transcript: {transcript}")
        return

    # Держим в памяти только последний ход (от последнего user-сообщения до
    # конца) — transcript у долгих сессий бывает 200+ МБ, полный парс в список
    # раздувал процесс до 500+ МБ и приводил к OOM.
    records = []
    with open(transcript, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("type") == "user":
                records = [rec]
            elif records:
                records.append(rec)
    if not records:
        log("no user-input found")
        return

    trigger_idx = 0
    trig_msg = records[trigger_idx].get("message") or {}
    trig_content = trig_msg.get("content")
    if isinstance(trig_content, str):
        trig_text = trig_content
    elif isinstance(trig_content, list):
        trig_text = "".join(
            x.get("text", "") for x in trig_content
            if isinstance(x, dict) and x.get("type") == "text"
        )
    else:
        trig_text = ""

    chat_m = re.search(r'chat_id="(\d+)"', trig_text)
    if not chat_m:
        log("trigger is not channel-msg (likely watchdog nudge), skipping")
        return
    chat_id = chat_m.group(1)

    text_blocks = []
    sent_args = []
    for i in range(trigger_idx + 1, len(records)):
        if records[i].get("type") != "assistant":
            continue
        msg = records[i].get("message") or {}
        for ti, t in enumerate(get_text_blocks(msg)):
            text_blocks.append((i, ti, t))
        sent_args.extend(get_tool_text_args(msg))

    if not text_blocks:
        log("no text blocks in this turn")
        return

    lost = [t for (_, _, t) in text_blocks if not text_already_sent(t, sent_args)]
    if not lost:
        log(f"OK: {len(text_blocks)} text-блоков, все покрыты {len(sent_args)} TG-tool")
        return

    os.makedirs(MARKER_DIR, exist_ok=True)
    marker = f"{MARKER_DIR}/{USER}-{session_id}-{trigger_idx}.sent"
    if os.path.exists(marker):
        log(f"marker exists, skipping: {marker}")
        return

    token = None
    try:
        for line in open(ENV_FILE):
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                token = line.strip().split("=", 1)[1].strip("'\"")
                break
    except Exception:
        pass
    if not token:
        log("no token")
        return

    sent_count = 0
    total_failed = 0
    for idx, text in enumerate(lost):
        chunks = chunk_text(text, TG_LIMIT)
        for cidx, chunk in enumerate(chunks):
            resp = send_message(token, chat_id, chunk)
            ci = f"[{idx+1}/{len(lost)}.{cidx+1}/{len(chunks)}]" if len(chunks) > 1 else f"[{idx+1}/{len(lost)}]"
            if resp.get("ok"):
                sent_count += 1
                log(f"SENT {ci} chat_id={chat_id} len={len(chunk)} preview={chunk[:60]!r}")
            else:
                total_failed += 1
                log(f"API err {ci}: {resp.get('description', '?')} code={resp.get('error_code', '?')} chunk_len={len(chunk)}")
            time.sleep(0.5)

    if sent_count:
        with open(marker, "w") as f:
            f.write(f"sent={sent_count} failed={total_failed}\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"unhandled: {type(e).__name__}: {e}")
