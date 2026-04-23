#!/bin/bash
# Watchdog for Claude Code + Telegram tmux sessions on Ainur VPS.
# Runs every minute via cron. Handles multiple sessions (root + wife + ...).
# If Claude hits a stuck dialog, dismiss it. If session died or bun died, restart.

LOG="/var/log/claude-tg-watchdog.log"

# Sessions: "tmux_session_name|linux_user|claude_cwd"
SESSIONS=(
  "claude-tg|root|/root"
  "claude-tg-wife|wife|/home/wife"
)

for entry in "${SESSIONS[@]}"; do
  IFS='|' read -r SESSION USER CWD <<< "$entry"

  # tmux runs per-user; wrap tmux commands with sudo -u when not root
  if [[ "$USER" == "root" ]]; then
    TMUX_CMD="tmux"
  else
    TMUX_CMD="sudo -u $USER tmux"
  fi

  session_alive=$($TMUX_CMD ls 2>/dev/null | grep -F "$SESSION")

  # Session missing — start it
  if [[ -z "$session_alive" ]]; then
    echo "$(date -Iseconds) [$SESSION] session missing, starting" >> "$LOG"
    if [[ "$USER" == "root" ]]; then
      tmux new-session -d -s "$SESSION" -x 200 -y 50 \
        "export PATH=/root/.bun/bin:\$PATH && cd $CWD && claude -c --permission-mode auto --effort high --debug --channels plugin:telegram@claude-plugins-official 2>>/var/log/claude-tg-debug.log"
    else
      sudo -u "$USER" bash -lc "tmux new-session -d -s '$SESSION' -x 200 -y 50 'cd $CWD && claude -c --permission-mode auto --effort high --debug --channels plugin:telegram@claude-plugins-official 2>>/var/log/claude-tg-debug.log'"
    fi
    sleep 4
    $TMUX_CMD send-keys -t "$SESSION" "1" Enter 2>/dev/null
    continue
  fi

  pane=$($TMUX_CMD capture-pane -t "$SESSION" -p 2>/dev/null)

  # Pattern 1: rate-limit dialog. If it keeps coming back 5 minutes in a row,
  # the UI dialog is probably stuck (reset already happened) — restart session.
  STUCK_FILE="/tmp/claude-wd-$SESSION-ratelimit-stuck"
  if echo "$pane" | grep -qE "hit your limit|rate.?limit.?options|Stop and wait for limit"; then
    stuck_count=$(cat "$STUCK_FILE" 2>/dev/null || echo 0)
    stuck_count=$((stuck_count + 1))
    echo "$stuck_count" > "$STUCK_FILE"
    if [[ "$stuck_count" -ge 5 ]]; then
      echo "$(date -Iseconds) [$SESSION] rate-limit dialog stuck ${stuck_count}min, restarting session" >> "$LOG"
      $TMUX_CMD kill-session -t "$SESSION" 2>/dev/null
      sleep 2
      if [[ "$USER" == "root" ]]; then
        tmux new-session -d -s "$SESSION" -x 200 -y 50 \
          "export PATH=/root/.bun/bin:\$PATH && cd $CWD && claude -c --permission-mode auto --effort high --debug --channels plugin:telegram@claude-plugins-official 2>>/var/log/claude-tg-debug.log"
      else
        sudo -u "$USER" bash -lc "tmux new-session -d -s '$SESSION' -x 200 -y 50 'cd $CWD && claude -c --permission-mode auto --effort high --channels plugin:telegram@claude-plugins-official'"
      fi
      sleep 4
      $TMUX_CMD send-keys -t "$SESSION" "1" Enter
      sleep 3
      $TMUX_CMD send-keys -t "$SESSION" "1" Enter
      rm -f "$STUCK_FILE"
    else
      $TMUX_CMD send-keys -t "$SESSION" Escape
      echo "$(date -Iseconds) [$SESSION] dismissed rate-limit dialog (${stuck_count}/5)" >> "$LOG"
    fi
    continue
  else
    rm -f "$STUCK_FILE"
  fi

  # Pattern 2: workspace trust dialog
  if echo "$pane" | grep -qE "Yes, I trust this folder"; then
    $TMUX_CMD send-keys -t "$SESSION" "1" Enter
    echo "$(date -Iseconds) [$SESSION] confirmed workspace trust" >> "$LOG"
    continue
  fi

  # Pattern 3: auto-mode-enable prompt
  if echo "$pane" | grep -qE "Enable auto mode\?|make it my default mode"; then
    $TMUX_CMD send-keys -t "$SESSION" "1" Enter
    echo "$(date -Iseconds) [$SESSION] enabled auto mode" >> "$LOG"
    continue
  fi

  # Pattern 5: "Resume from summary" prompt after session restart with -c
  # Summary mode is cheaper than full session resume for large .jsonl files.
  if echo "$pane" | grep -qE "Resume from summary|Resume full session as-is"; then
    $TMUX_CMD send-keys -t "$SESSION" "1" Enter
    echo "$(date -Iseconds) [$SESSION] chose resume-from-summary" >> "$LOG"
    continue
  fi

  # Pattern 4: telegram MCP (bun) died for >30sec → restart session.
  # Claude Code may restart MCP subprocess internally (seen around :23/:53).
  # Grace: wait 30s and recheck before killing the whole tmux session.
  if ! pgrep -u "$USER" -f "bun.*server\.ts" > /dev/null 2>&1; then
    sleep 30
    if ! pgrep -u "$USER" -f "bun.*server\.ts" > /dev/null 2>&1; then
      echo "$(date -Iseconds) [$SESSION] bun MCP not running 30s, restarting session" >> "$LOG"
      $TMUX_CMD kill-session -t "$SESSION" 2>/dev/null
      sleep 2
      if [[ "$USER" == "root" ]]; then
        tmux new-session -d -s "$SESSION" -x 200 -y 50 \
          "export PATH=/root/.bun/bin:\$PATH && cd $CWD && claude -c --permission-mode auto --effort high --debug --channels plugin:telegram@claude-plugins-official 2>>/var/log/claude-tg-debug.log"
      else
        sudo -u "$USER" bash -lc "tmux new-session -d -s '$SESSION' -x 200 -y 50 'cd $CWD && claude -c --permission-mode auto --effort high --debug --channels plugin:telegram@claude-plugins-official 2>>/var/log/claude-tg-debug.log'"
      fi
      sleep 4
      $TMUX_CMD send-keys -t "$SESSION" "1" Enter
    else
      echo "$(date -Iseconds) [$SESSION] bun MCP recovered within 30s, no action" >> "$LOG"
    fi
  fi
done

exit 0
