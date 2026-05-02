#!/bin/bash
# Install bun wrapper that injects --preload for telegram MCP plugin.
# Why: Anthropic plugin telegram@0.0.6 spawns bun via package.json "start"
# script (`bun install && bun server.ts`). The CHILD bun does not inherit
# --preload from the outer `bun run`, so we wrap the bun executable itself
# and inject --preload only when PWD or argv contains the plugin path.
#
# This survives plugin updates (file lives in /usr/local/share, not in
# plugin cache). Wrapper survives bun re-installs only until next `bun upgrade`
# or curl-install of bun — re-run this script after any bun update.
set -euo pipefail

PRELOAD_SRC="$(cd "$(dirname "$0")" && pwd)/telegram-mcp-heartbeat.ts"
PRELOAD_DST="/usr/local/share/telegram-mcp-heartbeat.ts"

if [[ ! -f "$PRELOAD_SRC" ]]; then
  echo "FATAL: $PRELOAD_SRC missing" >&2
  exit 1
fi

sudo install -o root -g root -m 0644 "$PRELOAD_SRC" "$PRELOAD_DST"
echo "[+] installed $PRELOAD_DST"

wrap_bun() {
  local BUN_PATH="$1"
  if [[ ! -f "$BUN_PATH" ]]; then
    echo "[--] no bun at $BUN_PATH, skip"
    return
  fi
  if file "$BUN_PATH" | grep -q "shell script"; then
    echo "[ok] $BUN_PATH already wrapped"
    return
  fi
  if [[ ! -e "${BUN_PATH}.real" ]]; then
    sudo mv "$BUN_PATH" "${BUN_PATH}.real"
  fi
  sudo tee "$BUN_PATH" > /dev/null <<WRAPPER
#!/bin/sh
case " \$PWD \$* " in
  *plugins/cache/claude-plugins-official/telegram*)
    case "\$1" in
      run)
        shift
        exec ${BUN_PATH}.real run --preload $PRELOAD_DST "\$@"
        ;;
      *.ts|*.js|*.mjs|*.tsx|*.jsx)
        exec ${BUN_PATH}.real --preload $PRELOAD_DST "\$@"
        ;;
      *)
        exec ${BUN_PATH}.real "\$@"
        ;;
    esac
    ;;
  *)
    exec ${BUN_PATH}.real "\$@"
    ;;
esac
WRAPPER
  sudo chmod 755 "$BUN_PATH"
  echo "[+] wrapped $BUN_PATH (real moved to ${BUN_PATH}.real)"
}

# Wrap every bun install we know about. Add more paths if bun lives elsewhere.
for p in /usr/local/bin/bun /root/.bun/bin/bun /home/*/.bun/bin/bun; do
  [[ -e "$p" ]] && wrap_bun "$p"
done

echo
echo "[i] To verify: pkill -9 -f bun.*server.ts; sleep 60"
echo "    then: stat ~/.claude/channels/telegram/bot.heartbeat"
echo "    mtime should be < 30s old."
