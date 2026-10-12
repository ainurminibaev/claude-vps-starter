#!/bin/bash
# Speech recognition for voice messages: language ID + Russian.
#
# Installs a small HTTP service on 127.0.0.1:9003 (systemd socket activation, user gigaam-asr):
#   * language ID — VoxLingua107 ECAPA (speechbrain/lang-id-voxlingua107-ecapa, Apache-2.0)
#   * Russian     — GigaAM v3 e2e CTC (salute-developers/GigaAM, MIT), int8, punctuation built in
# Both run as ONNX: the runtime needs only numpy + onnxruntime + sentencepiece (no torch, ~400 MB RAM loaded).
# The router /usr/local/bin/voice-to-text asks this service for the language; Russian goes to GigaAM,
# everything else to the optional Omnilingual service (tatar-asr/, 127.0.0.1:9002).
#
# Models are NOT downloaded ready-made from anyone's server. The installer fetches the original weights from
# their upstream sources (Sber CDN for GigaAM, Hugging Face for VoxLingua107 at a pinned revision), checks
# sha256 of every file BEFORE loading it, and converts them to ONNX in a temporary venv
# (torch + gigaam + speechbrain, ~2.5 GB disk) that is deleted afterwards.
#
# Usage:
#   sudo gigaam-asr/install-gigaam-asr.sh                 # install / update (idempotent)
#   sudo gigaam-asr/install-gigaam-asr.sh --force-model   # rebuild the models
#   sudo gigaam-asr/install-gigaam-asr.sh --uninstall     # remove service, user, files
#
# Overridable via environment:
#   INSTALL_DIR=/opt/gigaam-asr   PORT=9003   SVC_USER=gigaam-asr
#
# Needs: systemd, python3 >= 3.10, ~4 GB free disk while building (~0.35 GB stays),
# ~2.5 GB RAM+swap peak during the one-off ONNX export (run it when the server is quiet).
# Runtime: ~400 MB RAM while loaded (peak ~660 MB), the process exits after 10 min idle.

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/gigaam-asr}"
PORT="${PORT:-9003}"
SVC_USER="${SVC_USER:-gigaam-asr}"

# ---- pinned versions (the reference build was made with exactly these) ----
TORCH_SPEC='torch==2.11.0'
TORCH_CPU_INDEX='https://download.pytorch.org/whl/cpu'
ORT_SPEC='onnxruntime==1.31.0'
BUILD_SPECS=('speechbrain==1.1.1' 'onnx==1.23.2' 'onnxscript==0.7.2')
GIGAAM_COMMIT=7447938d791c4f3e643386ee22c33777004293a5
GIGAAM_GIT="git+https://github.com/salute-developers/GigaAM.git@${GIGAAM_COMMIT}"

# ---- upstream weights: URL + sha256 (sha256 computed from the upstream files; GigaAM additionally
#      checks its own md5 367074d6498f426d960b25f49531cf68 published in gigaam/__init__.py) ----
GIGAAM_CDN=https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM
VOX_REPO=speechbrain/lang-id-voxlingua107-ecapa
VOX_REV=0253049ae131d6a4be1c4f0d8b0ff483a0f8c8e9
VOX_BASE="https://huggingface.co/${VOX_REPO}/resolve/${VOX_REV}"
# format: <subdir> <file> <sha256> <url>
SOURCES="
gigaam v3_e2e_ctc.ckpt 73e27ec8f904e486ab063d5bb7c6ab701838b7e5a967ee79b71ccc5aefde146f $GIGAAM_CDN/v3_e2e_ctc.ckpt
gigaam v3_e2e_ctc_tokenizer.model 0b9a1960898fbfdf5424ab852ea17445eb3da960fba23e977ff100eb0054fbc8 $GIGAAM_CDN/v3_e2e_ctc_tokenizer.model
vox hyperparams.yaml 88fec9791a8416a152fb10834327e18d38e5bf7a351e9b714e08cdc4af05de6f $VOX_BASE/hyperparams.yaml
vox embedding_model.ckpt ab750d5c06d713477045fa798fab5d33e959dbc0dfe4de510a9a47844c79a19a $VOX_BASE/embedding_model.ckpt
vox classifier.ckpt a50d9024ff58d317031c9787d4c6c614d454a87a8ef32f9d36338cd3ff57adbc $VOX_BASE/classifier.ckpt
vox label_encoder.txt 9f566d83c4f19168be4a0bf86c0c7dac7d3264a95105bcbf33a7c32b83ccc17f $VOX_BASE/label_encoder.txt
"
# smoke test sample (Russian speech, from the GigaAM authors)
EXAMPLE_URL=$GIGAAM_CDN/example.wav
EXAMPLE_SHA=d8aaaa18a5098d7c6de0595ae7ac1e64cacd0d4022af3595213bdaf23be77e69
# built files: these two must match exactly; the ONNX graphs are compared for information only
# (with the pinned versions lid.onnx was reproduced byte-for-byte on a second machine)
# (another CPU / library build may produce a byte-different but equivalent file)
EXPECT_EXACT="
tokenizer.model 0b9a1960898fbfdf5424ab852ea17445eb3da960fba23e977ff100eb0054fbc8
lid_labels.txt ce47fc9a94ffb272516546e879f9c132cc95863a1d26864fbed05ef95980773a
"
EXPECT_REF="
lid.onnx 403e729d2b3ab7adc566bda2795303f16f0d0ae1d97dc5ad3cafd8bb2c71d01d
gigaam.int8.onnx 09c6fe4e8a6d7f71d76ba75ab3c6f4a17daf40f59f981af4b27d29c0eb6d1ee3
"

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SRC_DIR/.." && pwd)"
VENV="$INSTALL_DIR/venv"
PY="$VENV/bin/python"
MODEL_DIR="$INSTALL_DIR/models"
BUILD_DIR="$INSTALL_DIR/build"
UNIT_DIR=/etc/systemd/system
VTT_CONF=/etc/voice-to-text.conf
ROUTER_DST=/usr/local/bin/voice-to-text

MODE=install
FORCE_MODEL=0
for a in "$@"; do
  case "$a" in
    --uninstall)   MODE=uninstall ;;
    --force-model) FORCE_MODEL=1 ;;
    -h|--help)     sed -n '2,27p' "$0"; exit 0 ;;
    *) echo "unknown arg: $a" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "run as root (sudo $0)" >&2
  exit 1
fi
case "$INSTALL_DIR" in
  /root|/root/*|/home|/home/*)
    echo "INSTALL_DIR must not be under /root or /home (service runs with ProtectHome=yes)" >&2
    exit 1 ;;
esac

# set KEY=value in /etc/voice-to-text.conf (create / replace line)
set_conf() {
  local key="$1" val="$2"
  touch "$VTT_CONF"
  chmod 644 "$VTT_CONF"
  if grep -q "^${key}=" "$VTT_CONF"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$VTT_CONF"
  else
    echo "${key}=${val}" >> "$VTT_CONF"
  fi
}

# ---------------------------------------------------------------- uninstall
if [[ "$MODE" == uninstall ]]; then
  echo "=== gigaam-asr: uninstall ==="
  systemctl disable --now gigaam-asr.socket gigaam-asr.service 2>/dev/null || true
  rm -f "$UNIT_DIR/gigaam-asr.socket" "$UNIT_DIR/gigaam-asr.service"
  systemctl daemon-reload
  [ -f "$VTT_CONF" ] && set_conf GIGAAM_ENABLED 0
  echo "[+] service removed (GIGAAM_ENABLED=0 in $VTT_CONF; voice-to-text uses tatar-asr only, if installed)"
  if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
    echo "[+] removed $INSTALL_DIR"
  fi
  if id "$SVC_USER" >/dev/null 2>&1; then
    userdel "$SVC_USER" 2>/dev/null || true
    echo "[+] removed user $SVC_USER"
  fi
  exit 0
fi

# ---------------------------------------------------------------- install
echo "=== gigaam-asr: install ==="
echo "dir=$INSTALL_DIR port=$PORT user=$SVC_USER"
echo

# 1. Preconditions
command -v systemctl >/dev/null || { echo "systemd required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 required" >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "python3 >= 3.10 required" >&2; exit 1; }

need_model=1
if [ "$FORCE_MODEL" = 0 ] && [ -s "$MODEL_DIR/gigaam.int8.onnx" ] && [ -s "$MODEL_DIR/lid.onnx" ] \
   && [ -s "$MODEL_DIR/tokenizer.model" ] && [ -s "$MODEL_DIR/lid_labels.txt" ]; then
  need_model=0
fi
need_mb=300
[ "$need_model" = 1 ] && need_mb=$((need_mb + 4000))
check_dir="$INSTALL_DIR"
while [ ! -d "$check_dir" ]; do check_dir="$(dirname "$check_dir")"; done
free_mb=$(df -Pm "$check_dir" | awk 'NR==2{print $4}')
echo "1. Disk: ${free_mb} MB free on $(df -P "$check_dir" | awk 'NR==2{print $6}'), need ~${need_mb} MB"
if [ "$free_mb" -lt "$need_mb" ]; then
  echo "!!! not enough disk space, free some and re-run" >&2
  exit 1
fi
mem_mb=$(awk '/^(MemAvailable|SwapFree):/{s+=$2} END{print int(s/1024)}' /proc/meminfo)
if [ "$need_model" = 1 ] && [ "$mem_mb" -lt 3000 ]; then
  echo "[!] only ${mem_mb} MB RAM+swap available: the ONNX export needs ~2.5 GB peak, it may get OOM-killed"
fi
echo

# 2. System packages: ffmpeg (audio decoding in server.py), git (pip install of GigaAM), venv
apt_install() {
  command -v apt-get >/dev/null || { echo "install manually: $*" >&2; exit 1; }
  echo "[>] apt-get install $*"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@"
}
echo "2. System packages:"
pkgs=()
command -v ffmpeg >/dev/null || pkgs+=(ffmpeg)
command -v curl >/dev/null || pkgs+=(curl)
[ "$need_model" = 1 ] && ! command -v git >/dev/null && pkgs+=(git)
python3 -c 'import ensurepip' 2>/dev/null || pkgs+=(python3-venv)
if [ ${#pkgs[@]} -gt 0 ]; then apt_install "${pkgs[@]}"; else echo "[ok] ffmpeg, curl, python3-venv"; fi
echo

# 3. Service user + files
echo "3. User and files:"
if id "$SVC_USER" >/dev/null 2>&1; then
  echo "[ok] user $SVC_USER exists"
else
  useradd --system --no-create-home --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SVC_USER"
  echo "[+] created system user $SVC_USER"
fi
mkdir -p "$INSTALL_DIR" "$MODEL_DIR"
SERVER_CHANGED=0
if ! cmp -s "$SRC_DIR/server.py" "$INSTALL_DIR/server.py"; then
  install -m 755 "$SRC_DIR/server.py" "$INSTALL_DIR/server.py"
  SERVER_CHANGED=1
  echo "[+] server.py -> $INSTALL_DIR"
else
  echo "[ok] server.py up to date"
fi
echo

# 4. Runtime venv: numpy + onnxruntime + sentencepiece (no torch)
echo "4. Runtime venv:"
if [ ! -x "$VENV/bin/pip" ]; then
  rm -rf "$VENV"
  python3 -m venv "$VENV"
  echo "[+] created $VENV"
else
  echo "[ok] $VENV exists"
fi
if ! "$PY" -c 'import numpy, onnxruntime, sentencepiece' 2>/dev/null; then
  echo "[>] installing numpy, $ORT_SPEC, sentencepiece"
  "$PY" -m pip install -q --no-cache-dir numpy "$ORT_SPEC" sentencepiece
fi
"$PY" -c 'import onnxruntime, numpy; print("[ok] onnxruntime", onnxruntime.__version__, "/ numpy", numpy.__version__)'
echo

# 5. Models: download upstream weights (sha256-verified) -> ONNX export in a temporary venv -> delete it
fetch() {  # $1 dst, $2 sha256, $3 url
  local dst="$1" sha="$2" url="$3"
  if [ -s "$dst" ] && echo "$sha  $dst" | sha256sum -c --status; then
    echo "    [ok] $(basename "$dst") (cached)"; return 0
  fi
  echo "    [>] $url"
  curl -fL --retry 3 --retry-delay 3 --connect-timeout 20 -sS -o "$dst.part" "$url"
  if ! echo "$sha  $dst.part" | sha256sum -c --status; then
    echo "!!! sha256 mismatch for $(basename "$dst"): got $(sha256sum "$dst.part" | cut -d' ' -f1), expected $sha" >&2
    rm -f "$dst.part"
    echo "    The upstream file changed or the download is corrupted. Do not bypass the check:" >&2
    echo "    check the upstream release notes and update SOURCES in this script deliberately." >&2
    exit 1
  fi
  mv "$dst.part" "$dst"
  echo "    [ok] $(basename "$dst") sha256 verified"
}

echo "5. Models:"
if [ "$need_model" = 0 ]; then
  echo "[ok] models present in $MODEL_DIR (use --force-model to rebuild)"
else
  mkdir -p "$BUILD_DIR/src/gigaam" "$BUILD_DIR/src/vox" "$BUILD_DIR/out"
  echo "[>] downloading upstream weights (~530 MB)"
  while read -r sub file sha url; do
    [ -n "${sub:-}" ] || continue
    fetch "$BUILD_DIR/src/$sub/$file" "$sha" "$url"
  done <<< "$SOURCES"

  B="$BUILD_DIR/venv"
  if [ ! -x "$B/bin/pip" ]; then python3 -m venv "$B"; fi
  echo "[>] temporary build venv: $TORCH_SPEC (CPU), speechbrain, onnx, GigaAM@${GIGAAM_COMMIT:0:7} (~2 GB, a few minutes)"
  "$B/bin/pip" install -q --no-cache-dir "$TORCH_SPEC" 'torchaudio==2.11.0' --index-url "$TORCH_CPU_INDEX"
  "$B/bin/pip" install -q --no-cache-dir "hydra-core==1.3.*" "omegaconf==2.3.*" soundfile sentencepiece tqdm \
    "$ORT_SPEC" "${BUILD_SPECS[@]}"
  "$B/bin/pip" install -q --no-cache-dir --no-deps "$GIGAAM_GIT"
  install -m 644 "$SRC_DIR/export_models.py" "$BUILD_DIR/export_models.py"

  run_build() { (cd /tmp && HOME="$BUILD_DIR" PYTHONWARNINGS=ignore NUM_THREADS="$(nproc)" \
                 nice -n 10 "$B/bin/python" "$BUILD_DIR/export_models.py" "$@"); }
  echo "[>] VoxLingua107 -> ONNX"
  run_build lid "$BUILD_DIR/src/vox" "$BUILD_DIR/out"
  echo "[>] GigaAM v3_e2e_ctc -> ONNX fp32 (peak ~2.5 GB RAM)"
  run_build gigaam "$BUILD_DIR/src/gigaam" "$BUILD_DIR/out"
  echo "[>] GigaAM -> int8"
  run_build gigaam-quantize "$BUILD_DIR/out"

  while read -r f sha; do
    [ -n "${f:-}" ] || continue
    echo "$sha  $BUILD_DIR/out/$f" | sha256sum -c --status \
      || { echo "!!! $f: unexpected sha256 $(sha256sum "$BUILD_DIR/out/$f" | cut -d' ' -f1)" >&2; exit 1; }
  done <<< "$EXPECT_EXACT"
  while read -r f sha; do
    [ -n "${f:-}" ] || continue
    if echo "$sha  $BUILD_DIR/out/$f" | sha256sum -c --status; then
      echo "    [ok] $f identical to the reference build"
    else
      echo "    [i] $f differs from the reference build byte-wise (normal on another CPU/library build);"
      echo "        the smoke test below checks that it works"
    fi
  done <<< "$EXPECT_REF"
  for f in lid.onnx lid_labels.txt gigaam.int8.onnx tokenizer.model; do
    mv -f "$BUILD_DIR/out/$f" "$MODEL_DIR/$f"
  done
  rm -rf "$BUILD_DIR"
  echo "[+] models ready: $(du -sh "$MODEL_DIR" | cut -f1) in $MODEL_DIR, build venv and upstream weights deleted"
fi
find "$INSTALL_DIR" -name __pycache__ -type d -prune -exec rm -rf {} +
chown -R root:root "$INSTALL_DIR"
chmod -R a+rX "$INSTALL_DIR"
echo

# 6. systemd units (socket activation)
echo "6. systemd:"
UNITS_CHANGED=0
for u in gigaam-asr.socket gigaam-asr.service; do
  sed -e "s|/opt/gigaam-asr|$INSTALL_DIR|g" \
      -e "s|127\.0\.0\.1:9003|127.0.0.1:$PORT|g" \
      -e "s|^Environment=PORT=9003$|Environment=PORT=$PORT|" \
      -e "s|^User=gigaam-asr$|User=$SVC_USER|" \
      -e "s|^Group=gigaam-asr$|Group=$SVC_USER|" \
      "$SRC_DIR/$u" > "$UNIT_DIR/$u.new"
  if cmp -s "$UNIT_DIR/$u.new" "$UNIT_DIR/$u"; then
    rm -f "$UNIT_DIR/$u.new"
  else
    mv -f "$UNIT_DIR/$u.new" "$UNIT_DIR/$u"; chmod 644 "$UNIT_DIR/$u"; UNITS_CHANGED=1
  fi
done
[ "$UNITS_CHANGED" = 1 ] && systemctl daemon-reload
systemctl enable -q --now gigaam-asr.socket
echo "[+] gigaam-asr.socket enabled, listening on 127.0.0.1:$PORT"
# new code / unit / models -> restart only if it is running now (otherwise picked up on the next wake-up)
if [ "$SERVER_CHANGED$UNITS_CHANGED$need_model" != 000 ]; then
  systemctl try-restart gigaam-asr.service 2>/dev/null || true
fi
echo

# 7. Router + config
echo "7. voice-to-text router:"
if ! cmp -s "$REPO_DIR/scripts/voice-to-text" "$ROUTER_DST" 2>/dev/null; then
  install -m 755 "$REPO_DIR/scripts/voice-to-text" "$ROUTER_DST"
  echo "[+] installed $ROUTER_DST"
else
  echo "[ok] $ROUTER_DST up to date"
fi
set_conf GIGAAM_ENABLED 1
set_conf GIGAAM_URL "http://127.0.0.1:$PORT"
echo "[+] $VTT_CONF: GIGAAM_ENABLED=1, GIGAAM_URL=http://127.0.0.1:$PORT"
echo

# 8. Smoke test: Russian sample -> /detect-language must say ru, /asr must return text
echo "8. Smoke test (cold start ~2 s):"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
if curl -fsSL --retry 2 --connect-timeout 20 -o "$tmp/t.wav" "$EXAMPLE_URL" \
   && echo "$EXAMPLE_SHA  $tmp/t.wav" | sha256sum -c --status; then
  sample="GigaAM example.wav"
else
  echo "[i] could not fetch the sample, testing with a tone (checks only that the service answers)"
  ffmpeg -nostdin -loglevel error -f lavfi -i "sine=frequency=440:duration=2" -ar 16000 -ac 1 "$tmp/t.wav"
  sample=""
fi
code=$(curl -sS --max-time 120 -o "$tmp/lid" -w '%{http_code}' -F "audio_file=@$tmp/t.wav" \
  "http://127.0.0.1:$PORT/detect-language" || true)
if [ "$code" != 200 ]; then
  echo "[!] /detect-language answered '${code}': $(head -c 300 "$tmp/lid" 2>/dev/null)"
  echo "    logs: journalctl -u gigaam-asr.service -n 50 --no-pager"
  exit 1
fi
lang=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["language_code"], round(d.get("ru",0),2))' "$tmp/lid")
echo "[ok] /detect-language: $lang"
code=$(curl -sS --max-time 300 -o "$tmp/out" -w '%{http_code}' -F "audio_file=@$tmp/t.wav" \
  "http://127.0.0.1:$PORT/asr?language=ru" || true)
if [ "$code" != 200 ]; then
  echo "[!] /asr answered '${code}': $(head -c 300 "$tmp/out" 2>/dev/null)"
  echo "    logs: journalctl -u gigaam-asr.service -n 50 --no-pager"
  exit 1
fi
echo "[ok] /asr: $(head -c 200 "$tmp/out")"
if [ -n "$sample" ]; then
  case "$lang" in
    ru*) ;;
    *) echo "[!] $sample was not recognised as Russian — the LID model looks broken, rebuild with --force-model"; exit 1 ;;
  esac
  [ -s "$tmp/out" ] || { echo "[!] empty transcript for $sample — rebuild with --force-model"; exit 1; }
fi
echo
echo "Done. The service stays loaded for 10 min and then exits; the next request starts it again."
echo "Try:   voice-to-text /path/to/voice.oga            (auto: ru -> GigaAM, rest -> Omnilingual if installed)"
echo "Logs:  journalctl -t voice-to-text -n 20   |   journalctl -u gigaam-asr.service -n 50"
if ! systemctl is-enabled -q tatar-asr.socket 2>/dev/null; then
  echo "Optional: Tatar / English / Arabic and other languages -> sudo $REPO_DIR/tatar-asr/install-tatar-asr.sh"
fi
