#!/bin/bash
# Optional component: Tatar speech recognition for voice messages.
#
# Installs a small HTTP service on 127.0.0.1:9002 with the open model
# aadel4/omniASR-CTC-300M-v2 (Apache-2.0), quantized to int8 for CPU.
# The router /usr/local/bin/voice-to-text sends everything that the language ID in gigaam-asr
# (gigaam-asr/install-gigaam-asr.sh) does not recognise as Russian to this service.
#
# Usage:
#   sudo tatar-asr/install-tatar-asr.sh                 # install / update (idempotent)
#   sudo tatar-asr/install-tatar-asr.sh --force-model   # re-download and re-quantize the model
#   sudo tatar-asr/install-tatar-asr.sh --uninstall     # remove service, user, files; router back to GigaAM-only
#
# Overridable via environment:
#   INSTALL_DIR=/opt/tatar-asr   PORT=9002   SVC_USER=tatar-asr
#   MODEL_REPO=aadel4/omniASR-CTC-300M-v2   (HF_TOKEN / HF_ENDPOINT are honoured by huggingface_hub)
#
# Needs: systemd, python3 (3.9+), ~2 GB free disk while installing (fp32 download
# + int8 result; ~0.4 GB stays), +~1.5 GB if torch has to be installed into the venv.
# Runtime: ~1 GB RAM while loaded, the process exits after 15 min idle.

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/tatar-asr}"
PORT="${PORT:-9002}"
SVC_USER="${SVC_USER:-tatar-asr}"
MODEL_REPO="${MODEL_REPO:-aadel4/omniASR-CTC-300M-v2}"
TRANSFORMERS_SPEC='transformers>=5.3,<6'
TORCH_CPU_INDEX='https://download.pytorch.org/whl/cpu'

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SRC_DIR/.." && pwd)"
VENV="$INSTALL_DIR/venv"
PY="$VENV/bin/python"
MODEL_DIR="$INSTALL_DIR/model"
FP32_DIR="$INSTALL_DIR/fp32-tmp"
UNIT_DIR=/etc/systemd/system
VTT_CONF=/etc/voice-to-text.conf
ROUTER_DST=/usr/local/bin/voice-to-text

MODE=install
FORCE_MODEL=0
for a in "$@"; do
  case "$a" in
    --uninstall)   MODE=uninstall ;;
    --force-model) FORCE_MODEL=1 ;;
    -h|--help)     sed -n '2,22p' "$0"; exit 0 ;;
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
  echo "=== tatar-asr: uninstall ==="
  systemctl disable --now tatar-asr.socket tatar-asr.service 2>/dev/null || true
  rm -f "$UNIT_DIR/tatar-asr.socket" "$UNIT_DIR/tatar-asr.service"
  systemctl daemon-reload
  [ -f "$VTT_CONF" ] && set_conf TATAR_ENABLED 0
  echo "[+] service removed, router switched to GigaAM-only (TATAR_ENABLED=0 in $VTT_CONF)"
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
echo "=== tatar-asr: install ==="
echo "dir=$INSTALL_DIR port=$PORT user=$SVC_USER model=$MODEL_REPO"
echo

# 1. Preconditions
command -v systemctl >/dev/null || { echo "systemd required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 required" >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || { echo "python3 >= 3.9 required" >&2; exit 1; }

need_model=1
if [ -s "$MODEL_DIR/model_int8.pt" ] && [ "$FORCE_MODEL" = 0 ]; then
  need_model=0
fi
need_torch=1
if [ -x "$PY" ] && "$PY" -c 'import torch' 2>/dev/null; then
  need_torch=0
elif [ ! -x "$PY" ] && python3 -c 'import torch' 2>/dev/null; then
  need_torch=0   # venv uses --system-site-packages, system torch will be visible
fi

need_mb=200
[ "$need_model" = 1 ] && need_mb=$((need_mb + 2000))
[ "$need_torch" = 1 ] && need_mb=$((need_mb + 1500))
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
  echo "[!] only ${mem_mb} MB RAM+swap available: quantization needs ~2.5 GB peak, it may get OOM-killed"
fi
echo

# 2. System packages: ffmpeg (audio decoding in server.py)
apt_install() {
  command -v apt-get >/dev/null || { echo "install manually: $*" >&2; exit 1; }
  echo "[>] apt-get install $*"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@"
}
echo "2. System packages:"
if command -v ffmpeg >/dev/null; then
  echo "[ok] ffmpeg"
else
  apt_install ffmpeg
fi
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
install -m 755 "$SRC_DIR/server.py" "$INSTALL_DIR/server.py"
install -m 755 "$SRC_DIR/quantize.py" "$INSTALL_DIR/quantize.py"
echo "[+] server.py, quantize.py -> $INSTALL_DIR"
echo

# 4. Python venv (system site-packages: reuse system torch if there is one)
echo "4. Python venv:"
if [ ! -x "$VENV/bin/pip" ]; then
  rm -rf "$VENV"
  if ! python3 -m venv --system-site-packages "$VENV" 2>/dev/null; then
    rm -rf "$VENV"   # Debian/Ubuntu: venv module ships separately
    apt_install python3-venv
    python3 -m venv --system-site-packages "$VENV"
  fi
  echo "[+] created $VENV"
else
  echo "[ok] $VENV exists"
fi
if ! "$PY" -c 'import torch' 2>/dev/null; then
  echo "[>] installing torch (CPU build, ~200 MB download)"
  "$PY" -m pip install -q --no-cache-dir torch --index-url "$TORCH_CPU_INDEX"
fi
if ! "$PY" - <<'EOF' 2>/dev/null
import sys
import numpy, huggingface_hub, transformers
major, minor = (int(x) for x in transformers.__version__.split(".")[:2])
sys.exit(0 if (major, minor) >= (5, 3) and major < 6 else 1)
EOF
then
  echo "[>] installing $TRANSFORMERS_SPEC, numpy"
  "$PY" -m pip install -q --no-cache-dir "$TRANSFORMERS_SPEC" numpy
fi
"$PY" -c 'import torch, transformers; print("[ok] torch", torch.__version__, "/ transformers", transformers.__version__)'
echo

# 5. Model: download fp32 -> int8 quantization -> delete fp32
echo "5. Model:"
if [ "$need_model" = 0 ]; then
  echo "[ok] $MODEL_DIR/model_int8.pt exists (use --force-model to rebuild)"
else
  mkdir -p "$FP32_DIR"
  echo "[>] downloading $MODEL_REPO (~1.3 GB) to $FP32_DIR"
  MODEL_REPO="$MODEL_REPO" FP32_DIR="$FP32_DIR" "$PY" - <<'EOF'
import os
from huggingface_hub import hf_hub_download
repo, dst = os.environ["MODEL_REPO"], os.environ["FP32_DIR"]
for f in ("config.json", "vocab.json", "model.safetensors"):
    hf_hub_download(repo_id=repo, filename=f, local_dir=dst)
    print("   ", f, round(os.path.getsize(os.path.join(dst, f)) / 1e6, 1), "MB")
EOF
  echo "[>] quantizing to int8 (a few minutes)"
  MODEL_REPO="$MODEL_REPO" NUM_THREADS="$(nproc)" PYTHONWARNINGS=ignore \
    nice -n 10 "$PY" "$INSTALL_DIR/quantize.py" "$FP32_DIR" "$MODEL_DIR"
  rm -rf "$FP32_DIR"
  echo "[+] model ready: $(du -sh "$MODEL_DIR" | cut -f1) in $MODEL_DIR, fp32 copy deleted"
fi
find "$INSTALL_DIR" -name __pycache__ -type d -prune -exec rm -rf {} +
chown -R root:root "$INSTALL_DIR"
chmod -R a+rX "$INSTALL_DIR"
echo

# 6. systemd units (socket activation)
echo "6. systemd:"
for u in tatar-asr.socket tatar-asr.service; do
  sed -e "s|/opt/tatar-asr|$INSTALL_DIR|g" \
      -e "s|127\.0\.0\.1:9002|127.0.0.1:$PORT|g" \
      -e "s|^User=tatar-asr$|User=$SVC_USER|" \
      -e "s|^Group=tatar-asr$|Group=$SVC_USER|" \
      "$SRC_DIR/$u" > "$UNIT_DIR/$u"
done
systemctl daemon-reload
systemctl enable --now tatar-asr.socket
echo "[+] tatar-asr.socket enabled, listening on 127.0.0.1:$PORT"
if systemctl is-active -q tatar-asr.service; then
  echo "[!] tatar-asr.service is running the old code; new code is picked up after its idle exit"
  echo "    or now: systemctl restart tatar-asr.service"
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
set_conf TATAR_ENABLED 1
set_conf TATAR_URL "http://127.0.0.1:$PORT"
echo "[+] $VTT_CONF: TATAR_ENABLED=1, TATAR_URL=http://127.0.0.1:$PORT"
echo

# 8. Smoke test: 2 s tone -> /asr must answer 200 (first call includes the cold start)
echo "8. Smoke test (cold start ~10 s):"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
ffmpeg -nostdin -loglevel error -f lavfi -i "sine=frequency=440:duration=2" -ar 16000 -ac 1 "$tmp/t.wav"
code=$(curl -sS --max-time 300 -o "$tmp/out" -w '%{http_code}' -F "audio_file=@$tmp/t.wav" \
  "http://127.0.0.1:$PORT/asr" || true)
if [ "$code" = 200 ]; then
  echo "[ok] /asr answered 200"
  python3 -c 'import json,sys,urllib.request; print("    health:", urllib.request.urlopen(sys.argv[1], timeout=10).read().decode().strip())' \
    "http://127.0.0.1:$PORT/health" || true
else
  echo "[!] /asr answered '${code}': $(head -c 300 "$tmp/out" 2>/dev/null)"
  echo "    logs: journalctl -u tatar-asr.service -n 50 --no-pager"
  exit 1
fi
echo
echo "Done. The service stays loaded for 15 min and then exits; the next request starts it again."
echo "Try:   voice-to-text /path/to/voice.oga        (auto: ru -> GigaAM, rest -> Omnilingual)"
echo "       voice-to-text /path/to/voice.oga --lang tt"
echo "Logs:  journalctl -t voice-to-text -n 20   |   journalctl -u tatar-asr.service -n 50"
