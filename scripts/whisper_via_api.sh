#!/bin/bash
# Transcribe a voice file via local whisper-asr container (onerahmet/openai-whisper-asr-webservice).
# Matches the UX of `whisper <file> --language ru --output_format txt`.
set -euo pipefail
if [ -z "${1:-}" ]; then
  echo "usage: $0 <audio-file>" >&2
  exit 1
fi
curl -sS --fail-with-body -X POST \
  -F "audio_file=@${1}" \
  "http://127.0.0.1:9000/asr?language=ru&output=txt&encode=true"
