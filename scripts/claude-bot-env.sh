# Sourced by root's cron BASH_ENV and by the bot launcher. No shell options
# are changed, and ordinary task commands (including /usr/bin/claude) stay intact.
if [[ "${ANTHROPIC_BASE_URL:-}" == http://127.0.0.1:18418 ]]; then
  unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_MODEL
  unset ANTHROPIC_DEFAULT_OPUS_MODEL ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL
  unset CLAUDE_CODE_MAX_CONTEXT_TOKENS
  unset CLAUDE_CODE_AUTO_MODE_SERVER
fi
if [[ -f "$HOME/.claude/oauth-token-env" ]]; then
  source "$HOME/.claude/oauth-token-env"
fi
_claude_bot_backend=claude
if [[ -f "$HOME/.claude/model-backend" ]]; then
  IFS= read -r _claude_bot_backend < "$HOME/.claude/model-backend" || true
fi
if [[ "$_claude_bot_backend" == codex ]]; then
  source "$HOME/.config/codex-model-proxy/client.env"
  unset CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY
  export ANTHROPIC_BASE_URL=http://127.0.0.1:18418
  export ANTHROPIC_AUTH_TOKEN="${CODEX_PROXY_KEY:?Proxy client key missing}"
  unset CODEX_PROXY_KEY
  export ANTHROPIC_MODEL=gpt-6.1-sol
  export ANTHROPIC_DEFAULT_OPUS_MODEL="$ANTHROPIC_MODEL"
  export ANTHROPIC_DEFAULT_SONNET_MODEL="$ANTHROPIC_MODEL"
  export ANTHROPIC_DEFAULT_HAIKU_MODEL="$ANTHROPIC_MODEL"
  export CLAUDE_CODE_MAX_CONTEXT_TOKENS=475000
  # Keep client-side Auto Mode checks without requesting Anthropic safeguards.
  export CLAUDE_CODE_AUTO_MODE_SERVER=0
fi
unset _claude_bot_backend
