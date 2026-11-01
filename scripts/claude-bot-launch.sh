#!/usr/bin/env bash
# Keep the normal Claude login intact; select Codex only for this user's bot.
set -euo pipefail
cli="${CLAUDE_BOT_CLI_BIN:-/usr/bin/claude}"

backend=claude
if [[ -f "$HOME/.claude/model-backend" ]]; then
  IFS= read -r backend < "$HOME/.claude/model-backend" || true
fi

case "$backend" in
  claude)
    source "${CLAUDE_BOT_ENV_FILE:-/usr/local/lib/claude-bot-cron/env.sh}"
    exec "$cli" "$@"
    ;;
  codex)
    source "${CLAUDE_BOT_ENV_FILE:-/usr/local/lib/claude-bot-cron/env.sh}"

    args=()
    if [[ -n "${CODEX_BOT_EFFORT:-}" ]]; then
      case "$CODEX_BOT_EFFORT" in
        low|medium|high|xhigh|max) ;;
        *) echo 'Invalid CODEX_BOT_EFFORT' >&2; exit 2 ;;
      esac
      args+=(--effort "$CODEX_BOT_EFFORT")
    fi
    while (($#)); do
      case "$1" in
        --model)
          if (($# < 2)); then echo '--model requires a value' >&2; exit 2; fi
          shift 2
          ;;
        --model=*) shift ;;
        --effort)
          if (($# < 2)); then echo '--effort requires a value' >&2; exit 2; fi
          if [[ -z "${CODEX_BOT_EFFORT:-}" ]]; then args+=("$1" "$2"); fi
          shift 2
          ;;
        --effort=*)
          if [[ -z "${CODEX_BOT_EFFORT:-}" ]]; then args+=("$1"); fi
          shift
          ;;
        *) args+=("$1"); shift ;;
      esac
    done
    # A separate proxy service lets interactive and headless runs share the
    # same transport. The absolute binary also avoids cron shim recursion.
    exec "$cli" --model "$ANTHROPIC_MODEL" "${args[@]}"
    ;;
  *) echo "Unknown bot backend: $backend (expected claude or codex)" >&2; exit 2 ;;
esac
