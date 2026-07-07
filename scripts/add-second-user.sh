#!/bin/bash
# Add a second (or Nth) Claude Code + Telegram user on this VPS — fully automated.
#
# Usage:
#   sudo ./add-second-user.sh <username> \
#       --tg-token <bot_token> \
#       --tg-user-id <owner_tg_id> \
#       [--extra-allow <id>[,<id>...]]
#
# Prerequisites:
#   1. Bot created in @BotFather, token saved.
#   2. Owner's TG numeric ID known (via @userinfobot).
#   3. Owner has pressed /start at the bot at least once
#      (otherwise TG has no chat_id for them and getChat returns "chat not found").
#
# What it does (idempotent):
#   1. Creates linux user (locked password — all interaction via sudo -u).
#   2. Populates /home/<user>/.claude/ skeleton:
#      - settings.json (auto-mode, telegram plugin, Stop hook)
#      - hooks/stop-autoreply.py + scripts/send_tg.sh
#      - channels/telegram/.env (chmod 600) + access.json (allowlist)
#      - .claude.json (hasCompletedOnboarding=true, bypassPermissionsModeAccepted=true)
#   3. Adds watchdog SESSIONS entry with a fresh UUID (repo + deployed).
#   4. Registers user in auth-bot BOTS dict (repo + deployed) and restarts auth-bot.
#   5. Runs a verification pass and prints the next OAuth step.
#
# Everything writes to files owned by <username> so sudo -u <username> works cleanly.

set -e

USER=""
TG_TOKEN=""
TG_USER_ID=""
EXTRA_ALLOW=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tg-token)     TG_TOKEN="$2"; shift 2 ;;
    --tg-user-id)   TG_USER_ID="$2"; shift 2 ;;
    --extra-allow)  EXTRA_ALLOW="$2"; shift 2 ;;
    -h|--help)      grep '^#' "$0" | head -30; exit 0 ;;
    *)
      if [[ -z "$USER" ]]; then USER="$1"; shift
      else echo "unknown arg: $1"; exit 1; fi
      ;;
  esac
done

if [[ -z "$USER" ]] || [[ -z "$TG_TOKEN" ]] || [[ -z "$TG_USER_ID" ]]; then
  echo "usage: sudo $0 <username> --tg-token <bot_token> --tg-user-id <owner_tg_id> [--extra-allow <id>,<id>]"
  exit 1
fi

if [[ $EUID -ne 0 ]]; then
  echo "must run as root (via sudo)"
  exit 1
fi

HOME_DIR="/home/$USER"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WATCHDOG_REPO="$REPO_DIR/scripts/claude-tg-watchdog.sh"
WATCHDOG_DEPLOYED="/root/claude-tg-watchdog.sh"
AUTHBOT_REPO="$REPO_DIR/auth-bot/auth_bot.py"
AUTHBOT_DEPLOYED="/usr/local/bin/auth-bot.py"
SESSION_NAME="claude-tg-$USER"

# Build JSON allowlist array from --tg-user-id + --extra-allow
ALLOW_JSON="\"$TG_USER_ID\""
if [[ -n "$EXTRA_ALLOW" ]]; then
  IFS=',' read -ra EXTRA_IDS <<< "$EXTRA_ALLOW"
  for id in "${EXTRA_IDS[@]}"; do
    ALLOW_JSON+=", \"$id\""
  done
fi

echo "=== add-second-user v2 ==="
echo "User:      $USER"
echo "Bot token: ${TG_TOKEN:0:15}..."
echo "Allowlist: [$ALLOW_JSON]"
echo

# --- 1. Linux user ---
if id "$USER" >/dev/null 2>&1; then
  echo "[ok] user $USER exists"
else
  useradd -m -s /bin/bash "$USER"
  passwd -l "$USER" >/dev/null   # lock password — all interaction via sudo -u
  echo "[+] created user $USER (password locked)"
fi

# --- 2. Skeleton .claude/ ---
sudo -u "$USER" mkdir -p \
  "$HOME_DIR/.claude/hooks" \
  "$HOME_DIR/.claude/scripts" \
  "$HOME_DIR/.claude/channels/telegram"

tee "$HOME_DIR/.claude/settings.json" > /dev/null <<EOF
{
  "permissions": {
    "allow": [
      "mcp__plugin_telegram_telegram__download_attachment",
      "mcp__plugin_telegram_telegram__edit_message",
      "mcp__plugin_telegram_telegram__react",
      "mcp__plugin_telegram_telegram__reply"
    ],
    "defaultMode": "auto"
  },
  "enabledPlugins": { "telegram@claude-plugins-official": true },
  "effortLevel": "high",
  "theme": "dark",
  "skipAutoPermissionPrompt": true,
  "extraKnownMarketplaces": {
    "claude-plugins-official": {
      "source": { "source": "github", "repo": "anthropics/claude-plugins-official" }
    }
  },
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command", "command": "$HOME_DIR/.claude/hooks/stop-autoreply.py", "timeout": 15 } ] }
    ]
  }
}
EOF

cp "$REPO_DIR/hooks/stop-autoreply.py" "$HOME_DIR/.claude/hooks/"
chmod +x "$HOME_DIR/.claude/hooks/stop-autoreply.py"
cp "$REPO_DIR/scripts/send_tg.sh" "$HOME_DIR/.claude/scripts/"
chmod +x "$HOME_DIR/.claude/scripts/send_tg.sh"

# send_tg.sh has a hardcoded /root/.claude/... .env path; rewrite for this user
sed -i "s|\. /root/\.claude/channels/telegram/\.env|. $HOME_DIR/.claude/channels/telegram/.env|" \
  "$HOME_DIR/.claude/scripts/send_tg.sh"

tee "$HOME_DIR/.claude/channels/telegram/.env" > /dev/null <<EOF
TELEGRAM_BOT_TOKEN=$TG_TOKEN
EOF
chmod 600 "$HOME_DIR/.claude/channels/telegram/.env"

tee "$HOME_DIR/.claude/channels/telegram/access.json" > /dev/null <<EOF
{
  "dmPolicy": "allowlist",
  "allowFrom": [$ALLOW_JSON],
  "groups": {}
}
EOF

# .claude.json — pre-seed to skip onboarding and bypass modal
if [ ! -f "$HOME_DIR/.claude.json" ]; then
  tee "$HOME_DIR/.claude.json" > /dev/null <<'EOF'
{"hasCompletedOnboarding": true, "bypassPermissionsModeAccepted": true}
EOF
fi

chown -R "$USER:$USER" "$HOME_DIR/.claude" "$HOME_DIR/.claude.json"
echo "[+] .claude/ skeleton ready (settings, hooks, scripts, telegram env+access, .claude.json)"

# --- 3. Watchdog SESSIONS ---
if grep -qF "$SESSION_NAME|$USER|$HOME_DIR" "$WATCHDOG_REPO"; then
  echo "[ok] $SESSION_NAME already in watchdog SESSIONS"
else
  SESSION_UUID=$(uuidgen)
  echo "[>] adding $SESSION_NAME to watchdog (uuid: $SESSION_UUID)"
  sed -i "/^SESSIONS=(/,/^)/ { /^)/ i\\
  \"$SESSION_NAME|$USER|$HOME_DIR|$SESSION_UUID\"
  }" "$WATCHDOG_REPO"
  cp "$WATCHDOG_REPO" "$WATCHDOG_DEPLOYED"
  chmod +x "$WATCHDOG_DEPLOYED"
  echo "[+] watchdog SESSIONS updated (repo + deployed)"
fi

# --- 4. auth-bot BOTS registry ---
if [ -f "$AUTHBOT_REPO" ]; then
  python3 - "$AUTHBOT_REPO" "$AUTHBOT_DEPLOYED" "$USER" "$SESSION_NAME" "$HOME_DIR" <<'PY'
import sys, re, os
authbot_repo, authbot_deployed, u, s, home = sys.argv[1:]
entry = f'    "{u}":  {{"user": "{u}",            "session": "{s}",             "creds": "{home}/.claude/.credentials.json"}},\n'

def patch(path):
    if not os.path.exists(path):
        print(f"[skip] {path} not found")
        return
    src = open(path).read()
    if f'"{u}":' in src:
        print(f"[ok] {path} already has {u}")
        return
    # insert before closing "}" that terminates the BOTS dict
    m = re.search(r'(BOTS\s*=\s*\{.*?)(\n\})', src, re.DOTALL)
    if not m:
        print(f"[!] BOTS block not found in {path}")
        return
    new = src[:m.end(1)] + "\n" + entry + src[m.end(1):]
    open(path, "w").write(new)
    print(f"[+] patched {path}")

patch(authbot_repo)
patch(authbot_deployed)
PY

  if systemctl list-unit-files 2>/dev/null | grep -q '^auth-bot\.service'; then
    systemctl restart auth-bot
    echo "[+] auth-bot restarted"
  fi
else
  echo "[skip] auth-bot not installed at $AUTHBOT_REPO"
fi

# --- 5. verification ---
echo
echo "=== verification ==="
ok() { printf "  %-28s %s\n" "$1" "$2"; }
check() {
  local label="$1"; shift
  if eval "$@" >/dev/null 2>&1; then ok "$label" "OK"; else ok "$label" "MISSING"; fi
}
check "user $USER"                      "id $USER"
check ".claude/settings.json"           "[ -f $HOME_DIR/.claude/settings.json ]"
check "stop-autoreply.py executable"    "[ -x $HOME_DIR/.claude/hooks/stop-autoreply.py ]"
check "send_tg.sh executable"           "[ -x $HOME_DIR/.claude/scripts/send_tg.sh ]"
check ".env chmod 600"                  "[ \$(stat -c '%a' $HOME_DIR/.claude/channels/telegram/.env) = 600 ]"
check "access.json"                     "[ -f $HOME_DIR/.claude/channels/telegram/access.json ]"
check ".claude.json"                    "[ -f $HOME_DIR/.claude.json ]"
check "watchdog SESSIONS"               "grep -qF '$SESSION_NAME|$USER' $WATCHDOG_DEPLOYED"
if [ -f "$AUTHBOT_DEPLOYED" ]; then
  check "auth-bot BOTS"                 "grep -qF '\"$USER\":' $AUTHBOT_DEPLOYED"
fi
echo -n "  Bot API alive:               "
curl -s "https://api.telegram.org/bot${TG_TOKEN}/getMe" | python3 -c "import sys,json; print('OK' if json.load(sys.stdin).get('ok') else 'FAIL')" 2>&1 || echo "FAIL (network?)"

echo
echo "=== NEXT STEP ==="
echo "1. Owner must have pressed /start at the bot at least once."
echo "   Check: curl -s 'https://api.telegram.org/bot${TG_TOKEN}/getChat?chat_id=$TG_USER_ID' | jq .ok"
echo "   If 'chat not found' → ask owner to open the bot and hit /start."
echo
echo "2. In Telegram, message the auth-bot: /login $USER"
echo "   It replies with an OAuth URL."
echo
echo "3. Open URL → sign in with your Claude subscription → copy the code."
echo
echo "4. Paste the code back into the auth-bot chat."
echo "   It auto-inserts into the pane and waits for 'Login successful'."
echo
echo "5. Within 30-60s, watchdog boots tmux session '$SESSION_NAME' with the"
echo "   correct --channels plugin:telegram flag and sends a first-boot nudge."
echo "   The channel becomes active."
echo
echo "6. Owner writes to the bot → bot replies."
echo
echo "Monitor: sudo tail -f /var/log/claude-tg-watchdog.log | grep $SESSION_NAME"
