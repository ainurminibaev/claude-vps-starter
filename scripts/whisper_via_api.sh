#!/bin/bash
# Old name kept for backward compatibility (CLAUDE.md of existing bots calls it).
# Delegates to the shared router voice-to-text (see scripts/voice-to-text):
# whisper for ru/en/ar, optional Tatar service on :9002.
#
# Lookup order: /usr/local/bin/voice-to-text (installed by install.sh),
# then voice-to-text next to this script (repo checkout), then the old
# behaviour — plain whisper language=ru — so a `git pull` without re-running
# install.sh never breaks voice messages.
set -euo pipefail
for c in /usr/local/bin/voice-to-text "$(dirname "$(readlink -f "$0")")/voice-to-text"; do
  [ -x "$c" ] && exec "$c" "$@"
done

if [ -z "${1:-}" ]; then
  echo "usage: $0 <audio-file>" >&2
  exit 1
fi
curl -sS --fail-with-body -X POST \
  -F "audio_file=@${1}" \
  "http://127.0.0.1:9000/asr?language=ru&output=txt&encode=true"
