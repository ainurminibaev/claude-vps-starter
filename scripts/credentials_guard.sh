#!/bin/bash
# Проверяет credentials.json root.
# Broken → запускает auto-login flow (login → URL в TG → waiter ждёт код → автоввод).
set -uo pipefail
CREDS=/root/.claude/.credentials.json
LOG=/var/log/credentials-guard.log
STATE_DIR=/var/log/credentials-guard
ALERT_CHAT_ID=105839411
WAITER=/root/.claude/scripts/auto_login_waiter.sh
WAITER_LOCK=/var/run/auto-login-waiter.lock
COOLDOWN_SEC=3600
mkdir -p "$STATE_DIR"

refresh_len=$(python3 -c "
import json,sys
try:
    d = json.load(open(\"$CREDS\"))
    print(len(str(d[\"claudeAiOauth\"].get(\"refreshToken\",\"\"))))
except: print(0)
" 2>/dev/null)
refresh_len=${refresh_len:-0}
ts=$(date -Iseconds)
now=$(date +%s)

if [ "$refresh_len" -gt "30" ]; then
  echo "[$ts] OK refreshToken=$refresh_len" >> "$LOG"
  rm -f "$STATE_DIR/last-alert"
else
  # уже работает waiter — не запускать второй
  if [ -f "$WAITER_LOCK" ] && kill -0 "$(cat $WAITER_LOCK 2>/dev/null)" 2>/dev/null; then
    echo "[$ts] BROKEN refreshToken=$refresh_len (waiter already running)" >> "$LOG"
    exit 0
  fi
  last_alert=0
  [ -f "$STATE_DIR/last-alert" ] && last_alert=$(cat "$STATE_DIR/last-alert" 2>/dev/null || echo 0)
  elapsed=$((now - last_alert))
  if [ "$elapsed" -ge "$COOLDOWN_SEC" ]; then
    echo "$now" > "$STATE_DIR/last-alert"
    echo "[$ts] BROKEN refreshToken=$refresh_len → запускаю waiter" >> "$LOG"
    nohup "$WAITER" >>"$LOG" 2>&1 &
    disown
  else
    echo "[$ts] BROKEN refreshToken=$refresh_len (cooldown $((COOLDOWN_SEC - elapsed))s)" >> "$LOG"
  fi
fi
