#!/bin/bash
# Auto-login waiter для root claude-tg.
# - Запускает /login → шлёт URL юзеру в TG
# - Ждёт код от юзера в pane (до 6 часов, мониторит каждые 5 сек)
# - Если код expired → авто-перезапуск /login + новая ссылка с пометкой "🔁"
# - Если пользователь сам перелогинился (refreshToken вернулся) → exit
set -uo pipefail
LOG=/var/log/credentials-guard.log
LOCK=/var/run/auto-login-waiter.lock
SESSION=claude-tg
ALERT_CHAT_ID=105839411
SEND_TG=/root/.claude/scripts/send_tg.sh
CREDS=/root/.claude/.credentials.json
MAX_LIFETIME_SEC=54000  # 15 часов

echo $$ > "$LOCK"
trap "rm -f $LOCK" EXIT

log() { echo "[$(date -Iseconds)] [waiter $$] $*" >> "$LOG"; }
log "started"

is_creds_ok() {
  python3 -c "
import json
try:
    d=json.load(open(\"$CREDS\"))
    rl=len(str(d[\"claudeAiOauth\"].get(\"refreshToken\",\"\")))
    print(1 if rl > 30 else 0)
except: print(0)
"
}

start_login_and_get_url() {
  tmux send-keys -t "$SESSION" Escape; sleep 1
  tmux send-keys -t "$SESSION" Escape; sleep 1
  tmux send-keys -t "$SESSION" -l "/login"; sleep 0.3
  tmux send-keys -t "$SESSION" Enter; sleep 3
  tmux send-keys -t "$SESSION" Enter; sleep 5  # option 1
  tmux capture-pane -t "$SESSION" -p -S -50 | tr -d "\n" | grep -oE "https://claude\.com/cai/oauth/authorize\?[^ ]+" | head -1
}

start_ts=$(date +%s)
attempt=0
while true; do
  attempt=$((attempt+1))
  # bail если пользователь сам залогинился руками
  if [ "$(is_creds_ok)" = "1" ]; then
    log "creds починились сами — exit"
    exit 0
  fi
  if [ $(($(date +%s) - start_ts)) -gt $MAX_LIFETIME_SEC ]; then
    log "max lifetime 6h, abandoning"
    "$SEND_TG" "$ALERT_CHAT_ID" "⏰ auto-login: 15 часов истекли, бросаю. Делай /login вручную."
    exit 1
  fi

  url=$(start_login_and_get_url)
  if [ -z "$url" ]; then
    log "URL не достали (попытка $attempt), retry через 60с"
    sleep 60
    continue
  fi
  log "URL получен (попытка $attempt)"

  if [ "$attempt" = "1" ]; then
    "$SEND_TG" "$ALERT_CHAT_ID" "🔐 root credentials.json протух. Перелогинься:

$url

Под praim199524@gmail.com (Max). Пришли код сюда (формат XXX#YYY) — я вставлю и подтвержу автоматически."
  else
    "$SEND_TG" "$ALERT_CHAT_ID" "🔁 Старая ссылка протухла. Держи свежую:

$url

Пришли код сюда — он живёт 10 минут."
  fi

  # Ждать код в pane (мониторим каждые 5с)
  url_sent_ts=$(date +%s)
  code=""
  while [ -z "$code" ]; do
    sleep 5
    # bail если пользователь сам залогинился
    if [ "$(is_creds_ok)" = "1" ]; then
      log "creds починились во время ожидания кода — exit"
      exit 0
    fi
    # bail если 6 часов общего жизни истекли
    if [ $(($(date +%s) - start_ts)) -gt $MAX_LIFETIME_SEC ]; then
      log "max lifetime, abandoning"
      exit 1
    fi
    pane=$(tmux capture-pane -t "$SESSION" -p)
    # код = последняя строка ← telegram · ... : <code>, где код матчит XXX#YYY
    candidate=$(echo "$pane" | grep -oE "← telegram · [^:]+: [A-Za-z0-9_-]{30,}#[A-Za-z0-9_-]{20,}" | tail -1)
    if [ -n "$candidate" ]; then
      code=$(echo "$candidate" | grep -oE "[A-Za-z0-9_-]{30,}#[A-Za-z0-9_-]{20,}" | tail -1)
      log "код получен через $(($(date +%s) - url_sent_ts))с после URL"
    fi
  done

  # Вставить код
  tmux send-keys -t "$SESSION" -l "$code"; sleep 0.5
  tmux send-keys -t "$SESSION" Enter; sleep 10

  pane_after=$(tmux capture-pane -t "$SESSION" -p)
  if echo "$pane_after" | grep -qE "Login successful|Logged in as"; then
    tmux send-keys -t "$SESSION" Enter; sleep 2
    # верификация что creds реально обновились
    sleep 2
    if [ "$(is_creds_ok)" = "1" ]; then
      fresh_refresh=$(python3 -c "
import json
d=json.load(open(\"$CREDS\"))
print(len(str(d[\"claudeAiOauth\"].get(\"refreshToken\",\"\"))))
")
      log "✅ login прошёл, refreshToken=$fresh_refresh"
      "$SEND_TG" "$ALERT_CHAT_ID" "✅ Авторизация успешна. Бот снова на связи (refreshToken=$fresh_refresh chars, +8h до expires)."
      tmux send-keys -t "$SESSION" -l "Перелогинились автоматически. Проверь TG inbox и ответь на пропущенное."
      sleep 0.3
      tmux send-keys -t "$SESSION" Enter
      exit 0
    fi
  fi

  # Не сработало — код expired или невалидный → перезапуск /login
  log "код не сработал (попытка $attempt) — буду генерить новую ссылку"
  sleep 3
  # → следующая итерация внешнего while: новый /login, новая ссылка, "🔁"
done
