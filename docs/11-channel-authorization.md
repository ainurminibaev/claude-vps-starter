# Auto Mode authorization for authenticated channels and delegated tasks

The native Claude CLI and its Telegram plugin are unchanged. CLIProxyAPI still
translates Anthropic Messages to Codex Responses. The primary model and the
separate Auto Mode classifiers both use the configured Codex backend.

## Observed failure

On 2026-11-01, root's finance task was blocked before reaching Google Sheets.
The parent and child denials cited `Unrequested Commit in a Connected App` and
`PII Data Handling`. A fresh approval through Telegram did not clear the child
denial: the classifier explicitly rejected an agent-relayed approval claim.
Connected-account credentials were not missing.

The native transcript records Telegram input with `origin.kind: channel` and
`isMeta: true`. Native classifier construction handles channel-origin events
differently from terminal human input. A subagent assignment is also not itself
a direct human instruction under the default User Intent Rule. The gateway
does not remove the original text. Different classifier models can interpret
these same provenance and consent rules differently; a compatible API does not
guarantee equivalent permission decisions. Native Claude users have also
[reported false denials](https://github.com/anthropics/claude-code/issues/97654).

## Scoped native configuration

`codex-backend/configure_channel_context.py` adds three managed entries to native
`autoMode.environment` and `autoMode.allow`. It does not rewrite classifier
verdicts, inject fake human approvals, change the CLI executable, add blanket
tool permission rules, or change project/application code.

- The environment entry names the existing `plugin:telegram:telegram` sender
  boundary. Sender IDs and group-specific sender scopes come from that user's
  existing private channel `access.json`, not an agent's claim.
- The first allow exception authorizes ordinary, task-relevant reads and
  reversible work on the user's own files and connected documents/reports when
  an actual request arrives inside that boundary. It does not require a second
  terminal message or another approval for every step.
- The second exception covers routine execution of the assigned task by a
  native subagent after its parent Agent permission gate. The assignment bounds
  this standing authorization; an assertion of broader approval does not expand
  it. This also covers task-relevant personal-data reads needed for the task.

Unrelated access, destructive actions, money movement, security/permission
changes, and publication/sharing outside the task are not granted by these
exceptions. Existing explicit user restrictions, `permissions` rules,
`soft_deny`, and `hard_deny` remain. Newly created sections include `$defaults`;
an operator's existing replacement arrays are preserved. These are model-read
policy exceptions, not a deterministic guarantee of every future decision.

This is standing task authorization configured by the operator. It changes
permission-policy interpretation; it is not merely a wire-format fix or a model
rename. See the native [configuration reference](https://code.claude.com/docs/en/auto-mode-config).

## Apply, switch back, and undo

Installed helper: `/usr/local/lib/claude-bot-cron/configure-channel-context.py`.
The installer copies it without changing any active profile. `bot-backend USER
codex` applies the managed rules from that user's existing channel sender scope;
`bot-backend USER claude` removes only those entries before selecting native
Claude. Profile switching still does not restart the bot or rewrite history.

For a one-off application as the target user:

```sh
python3 codex-backend/configure_channel_context.py --apply
```

The helper also accepts explicit `--settings` and `--access` paths for an
administrator configuring another existing user. It preserves file ownership
and permissions and writes atomically. Before the first application it creates
the private `settings.json.before-channel-context` backup. `--revert` removes
only entries with the managed prefix; other settings are kept. Re-run after
changing the Telegram sender allowlist, since the policy contains a snapshot.

Use `--export /private/path/policy.json` to inspect/test a proposal without
changing active settings. No credentials or user-specific policy snapshots
belong in the public fork. Do not respond to a rejected action by repeatedly
trying different tools or asking for the same natural-language approval again.

## Verification and limits

The investigation used isolated native classifier captures, then replayed only
synthetic authorization requests through the existing gateway. Represented
Google Sheets, mail, calendar, publication and shell actions were never
executed. The native capture itself uses only a harmless temporary-file write;
its follow-up-stage test can add an extra block, never an allow verdict.

With the VPS CLI 2.1.310 policy, delegated finance reading and calendar reading
were denied under the original policy and allowed under the proposed policy.
Other positive cases covered direct channel requests, a monthly-report Agent
assignment, email reading and starting a localhost development server.
Negative controls cover a different sender, forged assistant approval,
unrequested publication and a subagent changing SSH access outside its task.
All four realistic VPS-policy controls returned native `<block>yes</block>`.
An earlier control used a nonnumeric fake sender and returned no parseable
verdict; it is retained in the private report and excluded from pass counts.
The realistic test uses a numeric sender outside the existing allowlist. The
probe counts only explicit `yes`/`no` verdicts, never a missing/malformed answer.

A separate synthetic private-key-exfiltration classification was rejected by
the upstream API with HTTP 400 before a classifier verdict. It is excluded
from classifier pass counts; it is not proof of an Auto Mode verdict and must
not be treated as successful authorization. This does not establish general
safety equivalence or cover every client/model version.

`tests/test_channel_context.py` checks idempotence, revert, sender scoping,
retention of default/custom deny rules, and intentional empty replacement
arrays. Native fixtures, account-specific IDs, full client policies and detailed
reports stay in private `auto-mode-audit` directories on Mac/VPS.
