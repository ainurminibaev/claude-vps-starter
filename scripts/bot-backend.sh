#!/usr/bin/env bash
# bot-backend <user> [claude|codex]; switching does not restart the bot.
set -euo pipefail
if (($# < 1 || $# > 2)); then
  echo 'Usage: bot-backend <user> [claude|codex]' >&2; exit 2
fi
u="$1"
h=$(getent passwd "$u" | cut -d: -f6)
[[ -n "$h" && -d "$h/.claude" ]] || { echo "Unknown bot user: $u" >&2; exit 2; }
marker="$h/.claude/model-backend"
if (($# == 1)); then
  if [[ -f "$marker" ]]; then cat "$marker"; else echo claude; fi
  exit 0
fi
case "$2" in
  claude)
    rm -f -- "$marker"
    ;;
  codex)
    [[ -s "$h/.config/codex-model-proxy/client.env" ]] || {
      echo "Codex proxy access has not been configured for $u" >&2; exit 1;
    }
    systemctl is-active --quiet codex-model-proxy.service || {
      echo 'codex-model-proxy.service is not running' >&2; exit 1;
    }
    stage=$(mktemp "$h/.claude/model-backend.XXXXXX")
    printf '%s\n' codex > "$stage"
    chmod 0600 "$stage"
    chown "$u:$(id -gn "$u")" "$stage"
    mv -f -- "$stage" "$marker"
    ;;
  *) echo 'Expected claude or codex' >&2; exit 2 ;;
esac
echo "$u: backend=$2. Apply with: bot-restart.sh $u"
