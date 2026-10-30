#!/usr/bin/env bash
# Install only the optional model transport. Does not select bot modes or restart bots.
# Usage: sudo -H bash codex-backend/install.sh [root ilshat wife ...]
set -euo pipefail
[[ "$EUID" == 0 && "$HOME" == /root ]] || { echo 'Run with sudo -H as root' >&2; exit 1; }
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || {
  echo 'This pinned installer supports Linux x86_64' >&2; exit 1;
}
for command_name in curl python3 tar install systemctl systemd-analyze; do
  command -v "$command_name" >/dev/null || { echo "Missing: $command_name" >&2; exit 1; }
done
repo_dir=$(cd "$(dirname "$0")/.." && pwd)
version=8.0.47
asset="CLIProxyAPI_${version}_linux_amd64_no-plugin.tar.gz"
release="https://github.com/router-for-me/CLIProxyAPI/releases/download/v${version}"
proxy_stage=$(mktemp -d /tmp/codex-proxy-install.XXXXXX)
trap 'rm -rf -- "$proxy_stage"' EXIT
if [[ -n "${CODEX_PROXY_BINARY:-}" ]]; then
  install -m 0755 "$CODEX_PROXY_BINARY" "$proxy_stage/cli-proxy-api"
else
curl --fail --silent --show-error --location --max-time 120 --output "$proxy_stage/$asset" "$release/$asset"
curl --fail --silent --show-error --location --max-time 30 --output "$proxy_stage/checksums.txt" "$release/checksums.txt"
python3 - "$proxy_stage" "$asset" <<'PY'
from pathlib import Path
import hashlib, sys, tarfile
stage, asset = Path(sys.argv[1]), sys.argv[2]
expected = next(line.split()[0] for line in (stage / 'checksums.txt').read_text().splitlines()
                if line.split()[-1] == asset)
if hashlib.sha256((stage / asset).read_bytes()).hexdigest() != expected:
    raise SystemExit('Release checksum mismatch')
with tarfile.open(stage / asset) as archive:
    for member in archive.getmembers():
        if member.name.startswith('/') or '..' in Path(member.name).parts or member.issym() or member.islnk():
            raise SystemExit('Unexpected archive entry')
print('Release SHA256 verified')
PY
tar --extract --gzip --file "$proxy_stage/$asset" --directory "$proxy_stage" --no-same-owner --no-same-permissions
fi
install -d -m 0755 /root/.local/bin /usr/local/lib/claude-bot-cron
install -m 0755 "$proxy_stage/cli-proxy-api" /root/.local/bin/.cli-proxy-api.next
mv -f /root/.local/bin/.cli-proxy-api.next /root/.local/bin/cli-proxy-api
for name in claude-bot-launch bot-backend; do
  install -m 0755 "$repo_dir/scripts/$name.sh" "/usr/local/bin/.$name.next"
  mv -f "/usr/local/bin/.$name.next" "/usr/local/bin/$name"
done
install -m 0644 "$repo_dir/scripts/claude-bot-env.sh" /usr/local/lib/claude-bot-cron/env.sh
users=("$@")
if ((${#users[@]} == 0)); then users=(root); fi
python3 "$repo_dir/codex-backend/prepare.py" --users "${users[@]}"
install -m 0644 "$repo_dir/codex-backend/codex-model-proxy.service" /etc/systemd/system/codex-model-proxy.service
systemd-analyze verify /etc/systemd/system/codex-model-proxy.service
systemctl daemon-reload
systemctl enable --now codex-model-proxy.service
echo 'Transport installed. See docs/08-codex-backend.md before selecting a bot backend.'
echo 'For an existing running service, apply binary updates with a restart when bots are idle.'
