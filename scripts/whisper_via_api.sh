#!/bin/bash
# Old name kept for backward compatibility (older CLAUDE.md files call it).
# Has nothing to do with Whisper any more: delegates to the shared router voice-to-text
# (see scripts/voice-to-text): language ID + GigaAM for Russian, optional Omnilingual (tatar-asr) for the rest.
#
# Lookup order: /usr/local/bin/voice-to-text (installed by install.sh),
# then voice-to-text next to this script (repo checkout).
set -euo pipefail
for c in /usr/local/bin/voice-to-text "$(dirname "$(readlink -f "$0")")/voice-to-text"; do
  [ -x "$c" ] && exec "$c" "$@"
done
echo "voice-to-text not found: run scripts/install.sh from claude-vps-starter (docs/03-voice.md)" >&2
exit 1
