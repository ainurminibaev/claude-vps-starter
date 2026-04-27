#!/bin/bash
# Watchdog for Claude Code + Telegram tmux sessions on Ainur VPS.
# Runs every minute via cron. Handles multiple sessions (root + wife + ...).
# Patterns:
#   1. rate-limit dialog stuck                 → Esc; if 5 min same, restart
#   2. workspace trust prompt                  → "1" Enter
#   3. auto-mode confirm prompt                → "1" Enter
#   4. bun MCP died for >30s                   → restart session + nudge
#   5. "Resume from summary" prompt            → "1" Enter
#   6. silent hang (pending msg + idle, no spinner, pane unchanged 3m)
#                                              → tier1 nudge; tier2 restart+nudge

LOG="/var/log/claude-tg-watchdog.log"
STATE_DIR="/var/run/claude-tg-wd"
mkdir -p "$STATE_DIR"

# Sessions: "tmux_session_name|linux_user|claude_cwd"
# Each restart generates a fresh UUID stored in $STATE_DIR/$SESSION-current-uuid
SESSIONS=(
  "claude-tg|root|/root"
  "claude-tg-wife|wife|/home/wife"
)

# regex of indicators meaning Claude is currently in a turn (don't touch)
SPINNER_RE='✻|✽|✶|✢|⏳|Brewing|Brewed|Sketching|Forging|Twisting|Musing|Generating|Spelunking|Cogitating|Compacting|Thinking|Working|Sketching|Choreographing|Imagining|Flowing|Germinating|Leavening|Noodling|Jitterbugging'

NUDGE_TEXT='Проверь Telegram: на накопившиеся неотвеченные сообщения (включая голосовые из inbox) — ответь по правилам из CLAUDE.md.'

restart_session() {
  local SESSION="$1" USER="$2" CWD="$3"
  local TMUX_CMD="$4"
  $TMUX_CMD kill-session -t "$SESSION" 2>/dev/null
  # Hard-kill any leftover claude process for this user — it holds the
  # session-id lock; without this, --session-id $UUID fails with
  # "Session ID is already in use" and tmux dies immediately.
  pkill -9 -u "$USER" -f "claude --session-id\|claude -c \|claude --channels" 2>/dev/null
  pkill -9 -u "$USER" -f "bun.*server\.ts" 2>/dev/null
  sleep 3
  # Generate fresh UUID — Claude Code marks recent jsonl as "in use" by mtime,
  # reusing the old UUID right after kill triggers "Session ID is already in use".
  local NEW_UUID
  NEW_UUID=$(uuidgen)
  echo "$NEW_UUID" > "$STATE_DIR/$SESSION-current-uuid"
  if [[ "$USER" == "root" ]]; then
    tmux new-session -d -s "$SESSION" -x 200 -y 50 \
      "export PATH=/root/.bun/bin:\$PATH && cd $CWD && claude --session-id $NEW_UUID --permission-mode auto --effort high --debug --channels plugin:telegram@claude-plugins-official 2>>/var/log/claude-tg-debug.log"
  else
    sudo -u "$USER" bash -lc "tmux new-session -d -s '$SESSION' -x 200 -y 50 'cd $CWD && claude --session-id $NEW_UUID --permission-mode auto --effort high --channels plugin:telegram@claude-plugins-official'"
  fi
  sleep 4
  $TMUX_CMD send-keys -t "$SESSION" "1" Enter 2>/dev/null
  sleep 3
  $TMUX_CMD send-keys -t "$SESSION" "1" Enter 2>/dev/null
}

send_nudge() {
  local SESSION="$1" TMUX_CMD="$2"
  $TMUX_CMD send-keys -t "$SESSION" "$NUDGE_TEXT" 2>/dev/null
  sleep 1
  $TMUX_CMD send-keys -t "$SESSION" Enter 2>/dev/null
}

for entry in "${SESSIONS[@]}"; do
  IFS='|' read -r SESSION USER CWD <<< "$entry"

  if [[ "$USER" == "root" ]]; then
    TMUX_CMD="tmux"
  else
    TMUX_CMD="sudo -u $USER tmux"
  fi

  session_alive=$($TMUX_CMD ls 2>/dev/null | grep -F "$SESSION")

  if [[ -z "$session_alive" ]]; then
    echo "$(date -Iseconds) [$SESSION] session missing, starting" >> "$LOG"
    restart_session "$SESSION" "$USER" "$CWD" "$TMUX_CMD"
    continue
  fi

  pane=$($TMUX_CMD capture-pane -t "$SESSION" -p 2>/dev/null)

  # Pattern 1: rate-limit dialog (stuck >5 min → restart)
  STUCK_FILE="$STATE_DIR/$SESSION-ratelimit-stuck"
  if echo "$pane" | grep -qE "hit your limit|rate.?limit.?options|Stop and wait for limit"; then
    stuck_count=$(cat "$STUCK_FILE" 2>/dev/null || echo 0)
    stuck_count=$((stuck_count + 1))
    echo "$stuck_count" > "$STUCK_FILE"
    if [[ "$stuck_count" -ge 5 ]]; then
      echo "$(date -Iseconds) [$SESSION] rate-limit stuck ${stuck_count}min → restart" >> "$LOG"
      restart_session "$SESSION" "$USER" "$CWD" "$TMUX_CMD"
      rm -f "$STUCK_FILE"
    else
      $TMUX_CMD send-keys -t "$SESSION" Escape 2>/dev/null
      echo "$(date -Iseconds) [$SESSION] dismissed rate-limit (${stuck_count}/5)" >> "$LOG"
    fi
    continue
  else
    rm -f "$STUCK_FILE"
  fi

  # Pattern 2: workspace trust dialog
  if echo "$pane" | grep -qE "Yes, I trust this folder"; then
    $TMUX_CMD send-keys -t "$SESSION" "1" Enter 2>/dev/null
    echo "$(date -Iseconds) [$SESSION] confirmed workspace trust" >> "$LOG"
    continue
  fi

  # Pattern 3: auto-mode-enable prompt
  if echo "$pane" | grep -qE "Enable auto mode\?|make it my default mode"; then
    $TMUX_CMD send-keys -t "$SESSION" "1" Enter 2>/dev/null
    echo "$(date -Iseconds) [$SESSION] enabled auto mode" >> "$LOG"
    continue
  fi

  # Pattern 5: "Resume from summary" prompt
  if echo "$pane" | grep -qE "Resume from summary|Resume full session as-is"; then
    $TMUX_CMD send-keys -t "$SESSION" "1" Enter 2>/dev/null
    echo "$(date -Iseconds) [$SESSION] chose resume-from-summary" >> "$LOG"
    continue
  fi

  # Pattern 4: bun MCP died — grace 30s then restart
  if ! pgrep -u "$USER" -f "bun.*server\.ts" > /dev/null 2>&1; then
    sleep 30
    if ! pgrep -u "$USER" -f "bun.*server\.ts" > /dev/null 2>&1; then
      echo "$(date -Iseconds) [$SESSION] bun MCP gone 30s → restart + nudge" >> "$LOG"
      restart_session "$SESSION" "$USER" "$CWD" "$TMUX_CMD"
      sleep 6
      send_nudge "$SESSION" "$TMUX_CMD"
      echo "$(date -Iseconds) [$SESSION] post-restart nudge sent" >> "$LOG"
    fi
    continue
  fi

  # Pattern 6: silent hang — pending message + no spinner + pane unchanged 3 min
  MD5_FILE="$STATE_DIR/$SESSION-pane-md5"
  COUNTER_FILE="$STATE_DIR/$SESSION-pane-nochange"
  current_md5=$(echo "$pane" | md5sum | awk '{print $1}')
  prev_md5=$(cat "$MD5_FILE" 2>/dev/null || echo "")
  if [[ "$current_md5" == "$prev_md5" ]]; then
    counter=$(cat "$COUNTER_FILE" 2>/dev/null || echo 0)
    counter=$((counter + 1))
  else
    counter=0
    echo "$current_md5" > "$MD5_FILE"
  fi
  echo "$counter" > "$COUNTER_FILE"

  # Find positions of last inbound vs last activity
  last_inbound_line=$(echo "$pane" | grep -n "← telegram" | tail -1 | cut -d: -f1)
  last_activity_line=$(echo "$pane" | grep -nE "● |Called plugin:telegram:telegram" | tail -1 | cut -d: -f1)
  has_spinner=$(echo "$pane" | grep -cE "$SPINNER_RE")

  pending=false
  if [[ -n "$last_inbound_line" ]]; then
    if [[ -z "$last_activity_line" ]] || [[ "$last_inbound_line" -gt "$last_activity_line" ]]; then
      pending=true
    fi
  fi

  if $pending && [[ "$has_spinner" -eq 0 ]] && [[ "$counter" -ge 3 ]]; then
    NUDGED_FILE="$STATE_DIR/$SESSION-nudged-at"
    last_nudge=$(cat "$NUDGED_FILE" 2>/dev/null || echo 0)
    now=$(date +%s)
    elapsed=$((now - last_nudge))
    if [[ "$elapsed" -ge 120 ]]; then
      # Tier 1: nudge first time, or 2 min after previous nudge
      if [[ "$last_nudge" -eq 0 ]] || [[ "$counter" -lt 6 ]]; then
        echo "$(date -Iseconds) [$SESSION] hang ${counter}min, sending nudge" >> "$LOG"
        send_nudge "$SESSION" "$TMUX_CMD"
        echo "$now" > "$NUDGED_FILE"
        echo "0" > "$COUNTER_FILE"
      else
        # Tier 2: nudge didn't help → restart + nudge
        echo "$(date -Iseconds) [$SESSION] hang persists, nudge ineffective → restart + nudge" >> "$LOG"
        restart_session "$SESSION" "$USER" "$CWD" "$TMUX_CMD"
        sleep 6
        send_nudge "$SESSION" "$TMUX_CMD"
        echo "$now" > "$NUDGED_FILE"
        echo "0" > "$COUNTER_FILE"
      fi
    fi
  fi

  # Cleanup nudge state when pane changes naturally
  if [[ "$counter" -eq 0 ]]; then
    rm -f "$STATE_DIR/$SESSION-nudged-at"
  fi
done

exit 0
