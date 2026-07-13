#!/usr/bin/env bash
set -euo pipefail

# 1) Uruchamiaj względem checkoutu, niezależnie od WorkingDirectory systemd.
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"
mkdir -p results/live
#exec >> results/live/service.log 2>&1

echo "[run_live] starting at $(date +'%F %T')"

# 2) Beta 0 runtime: przypięte uv + .python-version + uv.lock.
#    UV_BIN można ustawić na absolutną ścieżkę w unit file systemd.
UV_BIN="${UV_BIN:-uv}"
UV_VERSION_REQUIRED="0.11.28"

if ! command -v "$UV_BIN" >/dev/null 2>&1; then
  echo "[run_live] FATAL: uv not found; install uv ${UV_VERSION_REQUIRED} or set UV_BIN" >&2
  exit 1
fi

UV_OUTPUT="$("$UV_BIN" --version 2>/dev/null || true)"
UV_ACTUAL_VERSION="${UV_OUTPUT#uv }"
UV_ACTUAL_VERSION="${UV_ACTUAL_VERSION%% *}"
if [ "$UV_ACTUAL_VERSION" != "$UV_VERSION_REQUIRED" ]; then
  echo "[run_live] FATAL: expected uv ${UV_VERSION_REQUIRED}, got '${UV_OUTPUT:-unknown}'" >&2
  exit 1
fi

export PYTHONUNBUFFERED=1
export UV_NO_PROGRESS=1

# 3) Odpalenie legacy live runnera w dokładnie zablokowanym środowisku.
exec "$UV_BIN" run --locked python -m live.live_binance \
  --symbol BTC/USDT \
  --timeframe 5m \
  --strategy simple_momentum \
  --params '{"short":3,"long":6,"side":"short"}' \
  --size_usdt 150 \
  --leverage 3 \
  --data_source testnet \
  --tpsl_mode local \
  --price_feed mainnet_mark \
  --poll_ms 1000 \
  --cat_sl_pct 0.15
