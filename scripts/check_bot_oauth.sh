#!/bin/bash
# Check OAuth health of all 9 Claude-TG bot instances.
# If a bot shows "Please run /login" / "Invalid authentication" / "API Error: 401",
# send a notification to Ainur (chat 105839411) via direct Telegram Bot API.
# Cooldown: 1 alert per bot per 60 min.
#
# Schedule: */15 * * * * /root/.claude/scripts/check_bot_oauth.sh > /tmp/check_bot_oauth.last.log 2>&1

set -uo pipefail

ALERT_CHAT_ID="105839411"
STATE_DIR="/var/log/bot-oauth-state"
COOLDOWN_SEC=3600

mkdir -p "$STATE_DIR"

# user → tmux session name
# NOTE: root (claude-tg / my own bot) intentionally NOT monitored —
# (1) the script can't reliably distinguish my own quoted text "Invalid auth..."
#     from actual API errors, and (2) if my own bot is dead, I can't notify myself
#     anyway. I'll notice via direct tmux check or user telling me.
declare -A USERS=(
  [wife]="claude-tg-wife"
  [rafka]="claude-tg-rafka"
  [bulatov]="claude-tg-bulatov"
  [alfiya-mama-rafka]="claude-tg-alfiya-mama-rafka"
  [rishat-rafka-papa]="claude-tg-rishat-rafka-papa"
  [khazrat]="claude-tg-khazrat"
  [niyaz]="claude-tg-niyaz"
  [diana]="claude-tg-diana"
)

now=$(date +%s)
problems=()

for user in "${!USERS[@]}"; do
  session="${USERS[$user]}"
  # Capture ONLY the bottom 8 lines of the visible pane.
  # `tmux capture-pane -p` returns the whole visible terminal (~40 lines, includes history).
  # `tail -n 8` keeps just the last 8 — the input bar area + most recent status.
  # If "Invalid authentication" appears in the last 8 lines, it's the current state,
  # not a historical scroll line.
  pane=$(sudo -u "$user" tmux capture-pane -t "$session" -p 2>/dev/null | tail -n 8 || echo "")

  if [ -z "$pane" ]; then
    continue  # session missing — watchdog handles that separately
  fi

  # Match the specific Claude error string (unlikely to appear in normal bot output).
  # "Invalid authentication credentials" is the verbatim Anthropic API 401 response.
  if echo "$pane" | grep -qF "Invalid authentication credentials"; then
    state_file="$STATE_DIR/${user}.last-alert"
    last_alert=0
    [ -f "$state_file" ] && last_alert=$(cat "$state_file" 2>/dev/null || echo 0)

    elapsed=$((now - last_alert))
    if [ "$elapsed" -ge "$COOLDOWN_SEC" ]; then
      problems+=("$user")
      echo "$now" > "$state_file"
    fi
  else
    # OAuth healthy → clear state so next failure alerts immediately
    rm -f "$STATE_DIR/${user}.last-alert"
  fi
done

if [ ${#problems[@]} -gt 0 ]; then
  msg="⚠️ Протух OAuth Claude в ${#problems[@]} ботах:"
  for u in "${problems[@]}"; do
    msg="$msg
• $u"
  done
  msg="$msg

Зайди в tmux, /login. Например:
sudo -u <user> tmux attach -t claude-tg-<user>"

  /root/.claude/scripts/send_tg.sh "$ALERT_CHAT_ID" "$msg"
fi

