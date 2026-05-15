#!/usr/bin/env bash
set -euo pipefail

# 1) Przejdź do katalogu repo i włącz log do pliku (pomaga przy debug pod systemd)
cd /home/janek/trading/backtesting/algo_bot
mkdir -p results/live
#exec >> results/live/service.log 2>&1

echo "[run_live] starting at $(date +'%F %T')"

# 2) Aktywacja conda + env 'pandas_python'
#    — obsługujemy najczęstsze instalacje + fallback przez `conda info --base`
CONDA_BASE=""
if [ -x "$HOME/miniconda3/bin/conda" ]; then
  CONDA_BASE="$HOME/miniconda3"
elif [ -x "$HOME/anaconda3/bin/conda" ]; then
  CONDA_BASE="$HOME/anaconda3"
elif command -v conda >/dev/null 2>&1; then
  CONDA_BASE="$(conda info --base 2>/dev/null || true)"
fi

if [ -n "$CONDA_BASE" ] && [ -f "$CONDA_BASE/etc/profile.d/conda.sh" ]; then
  # standardowy hook
  # shellcheck disable=SC1090
  source "$CONDA_BASE/etc/profile.d/conda.sh"
  conda activate pandas_python
elif [ -n "$CONDA_BASE" ] && [ -x "$CONDA_BASE/bin/conda" ]; then
  # alternatywny hook (gdy profile.d nie istnieje)
  eval "$("$CONDA_BASE/bin/conda" shell.bash hook)"
  conda activate pandas_python
else
  echo "[run_live] FATAL: conda not found; add it to PATH or install Miniconda" >&2
  exit 1
fi

export PYTHONUNBUFFERED=1

# 4) Odpalenie bota (parametry edytuj według potrzeb)
exec python -m live.live_binance \
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

