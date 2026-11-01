# Per-user Codex model and effort

The VPS Codex profile can select a model without modifying the native Claude
binary or changing routing for other clients. In each bot user's private
`~/.config/codex-model-proxy/client.env`, keep the existing key and add:

```sh
CODEX_BOT_MODEL=gpt-6-astra
CODEX_BOT_EFFORT=medium
```

`claude-bot-env.sh` exports this model for bot and cron invocations. The launcher
replaces the watchdog's model and effort flags with the selected Codex profile.
Without these settings, the existing Sol default and caller's effort remain.
The gateway's optional classifier safeguards must cover the selected model too;
`codex-backend/config.example.yaml` covers both Sol and Astra. Ordinary work uses
the selected Medium effort; auxiliary reviews retain High and recognized Auto
Mode stage-one checks use Medium. This does not alter permission policy.

Restart only idle bots, preserving their stored session UUID and any unsent
input. Running processes retain their previous environment until restarted.
Do not remap the actual `gpt-6.1-sol` model name to Astra in the gateway.

Returning with `bot-backend USER claude` ignores these Codex-only settings;
the native watchdog arguments remain `--model opus --effort high`. Session
history, memory, Telegram access and cron definitions are unchanged. Switching
back to Sol can use `CODEX_BOT_MODEL=gpt-6.1-sol` and the desired effort instead.

On 2026-11-01 the operator requested Astra Medium for all eleven VPS bots.
The local Mac profile is independent and is not changed by this deployment.
