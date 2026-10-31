#!/usr/bin/env bash
# Build a pinned upstream plus the reviewed compatibility patches.
# Usage: GO_BIN=/path/to/go bash build.sh darwin arm64 /absolute/output
set -euo pipefail
target_os=${1:?darwin or linux}
target_arch=${2:?arm64 or amd64}
output=${3:?absolute output path}
[[ "$output" == /* ]] || { echo 'Output must be absolute' >&2; exit 1; }
case "$target_os/$target_arch" in darwin/arm64|linux/amd64) ;; *) exit 2 ;; esac
go_bin=${GO_BIN:-go}
patch_dir=$(cd "$(dirname "$0")" && pwd)
gateway_stage=$(mktemp -d "${TMPDIR:-/tmp}/cpa-build.XXXXXX")
trap 'rm -rf -- "$gateway_stage"' EXIT
git clone --quiet --depth 1 --branch v8.0.47 https://github.com/router-for-me/CLIProxyAPI.git "$gateway_stage/source"
cd "$gateway_stage/source"
[[ $(git rev-parse HEAD) == 90c447d3a8727d115aa7552ec93ce023736ed492 ]] || {
  echo 'Pinned upstream commit mismatch' >&2; exit 1;
}
git apply --check "$patch_dir/visible-commentary.patch"
git apply "$patch_dir/visible-commentary.patch"
git apply --check "$patch_dir/network-recovery.patch"
git apply "$patch_dir/network-recovery.patch"
git apply --check "$patch_dir/http2-protocol-recovery.patch"
git apply "$patch_dir/http2-protocol-recovery.patch"
git apply --check "$patch_dir/auto-mode-policy-tests.patch"
git apply "$patch_dir/auto-mode-policy-tests.patch"
CGO_ENABLED=0 "$go_bin" test ./internal/translator/codex/claude ./internal/runtime/executor ./internal/runtime/executor/helps ./sdk/cliproxy/auth
CGO_ENABLED=0 GOOS="$target_os" GOARCH="$target_arch" "$go_bin" build -trimpath \
  -ldflags '-s -w -X main.Version=8.0.47+codex-compat.4 -X main.Commit=90c447d-compat' \
  -o "$output" ./cmd/server
echo "Built $output"
